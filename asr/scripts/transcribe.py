#!/usr/bin/env python3
"""Resumable Japanese Parakeet CTC INT8 transcription; no Drive/OAuth operations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time

MODEL_SHA256 = "3addd00ef5bd1742078389e540b77394e4a508bdf2f4c9ad1b4a76d93e76598e"
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 20 * SAMPLE_RATE
MAX_ATTEMPTS = 3  # Initial attempt plus two retries.


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_text(path, text):
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".part", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def peak_rss_bytes():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def emit(output, event, **values):
    record = {"time_utc": utc_now(), "event": event, **values}
    line = json.dumps(record, ensure_ascii=False, allow_nan=False)
    with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(line, flush=True)


def intervals(total_frames):
    if type(total_frames) is not int or total_frames <= 0:
        raise RuntimeError("The decoded audio has no samples.")
    return [{"index": index, "start_frame": start, "end_frame": min(start + CHUNK_SAMPLES, total_frames),
             "start": start / SAMPLE_RATE, "end": min(start + CHUNK_SAMPLES, total_frames) / SAMPLE_RATE}
            for index, start in enumerate(range(0, total_frames, CHUNK_SAMPLES))]


def normalized_audio(source, output, source_sha, sf):
    wav = output / "audio16k.wav"
    metadata = output / "audio16k.json"
    if wav.is_file() and metadata.is_file():
        try:
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            info = sf.info(str(wav))
            if (isinstance(saved, dict) and saved.get("source_sha256") == source_sha and saved.get("sha256") == digest(wav)
                    and info.samplerate == SAMPLE_RATE and info.channels == 1
                    and info.frames == saved.get("frames") and info.frames > 0):
                return wav, int(info.frames)
        except (OSError, RuntimeError, ValueError):
            pass
    for attempt in range(1, MAX_ATTEMPTS + 1):
        fd, name = tempfile.mkstemp(prefix=".decoded-", suffix=".wav", dir=output)
        os.close(fd)
        temporary = Path(name)
        try:
            converted = subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-y", "-i", str(source),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(temporary)], capture_output=True, timeout=1800)
            if converted.returncode:
                raise RuntimeError("ffmpeg audio conversion failed.")
            info = sf.info(str(temporary))
            if info.samplerate != SAMPLE_RATE or info.channels != 1 or info.frames <= 0:
                raise RuntimeError("ffmpeg returned invalid mono audio.")
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            if digest(source) != source_sha:
                raise RuntimeError("Source audio changed during conversion.")
            temporary.replace(wav)
            atomic_json(metadata, {"source_sha256": source_sha, "sha256": digest(wav),
                                   "frames": int(info.frames), "sample_rate": SAMPLE_RATE})
            return wav, int(info.frames)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            emit(output, "conversion_retry" if attempt < MAX_ATTEMPTS else "conversion_failed", attempt=attempt)
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError("Audio conversion failed after three attempts; the original was preserved.") from None
        finally:
            temporary.unlink(missing_ok=True)


def load_checkpoint(output, interval, job_key):
    stem = f"chunk-{interval['index']:05d}"
    text_path, meta_path = output / (stem + ".txt"), output / (stem + ".json")
    try:
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        text = text_path.read_text(encoding="utf-8")
        if (not isinstance(saved, dict) or saved.get("job_key") != job_key
                or saved.get("status") not in ("ok", "uncertain_empty")
                or any(saved.get(key) != value for key, value in interval.items())
                or saved.get("text_sha256") != digest(text_path)
                or type(saved.get("wall_seconds")) not in (int, float)
                or not math.isfinite(saved["wall_seconds"]) or saved["wall_seconds"] < 0
                or type(saved.get("peak_rss_bytes")) is not int or saved["peak_rss_bytes"] < 0
                or (saved["status"] == "ok" and not text.strip())
                or (saved["status"] == "uncertain_empty" and text.strip())):
            return None
        return saved, text
    except (OSError, UnicodeError, ValueError):
        return None


def decode(recognizer, samples, output, index):
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            recognizer.decode_stream(stream)
            text = stream.result.text
            if not isinstance(text, str):
                raise RuntimeError("Recognizer did not return UTF-8 text.")
            text.encode("utf-8")
            if text.strip():
                return text, "ok", attempt, time.monotonic() - started
            if attempt == MAX_ATTEMPTS:
                return "", "uncertain_empty", attempt, time.monotonic() - started
            emit(output, "retry", index=index, attempt=attempt, reason="empty")
        except MemoryError:
            raise RuntimeError(f"ASR chunk {index} ran out of memory; no completion was recorded.") from None
        except Exception as error:
            emit(output, "retry" if attempt < MAX_ATTEMPTS else "chunk_failed",
                 index=index, attempt=attempt, error_type=type(error).__name__)
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"ASR chunk {index} failed after three attempts ({type(error).__name__}).") from None


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    whole, fraction = divmod(milliseconds, 1000)
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}.{fraction:03d}"


def run(args):
    import sherpa_onnx
    import soundfile as sf

    source = args.audio.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    model_dir = Path(__file__).resolve().parents[1] / ".runtime" / "models" / "parakeet-ja"
    model, tokens = model_dir / "model.int8.onnx", model_dir / "tokens.txt"
    if not source.is_file():
        raise RuntimeError("Input audio is not available yet.")
    if not model.is_file() or not tokens.is_file():
        raise RuntimeError("Parakeet INT8 model/tokens are missing.")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    if (source.parent == output and (source.name in {"audio16k.wav", "audio16k.json", "manifest.json", "result.json", "progress.jsonl", "raw_transcript.txt", ".lock"}
                                    or source.name.startswith("chunk-"))):
        raise RuntimeError("Input overlaps a generated output path; use a separate output directory.")
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another process is using this output directory.") from None
        started = time.monotonic()
        model_sha = digest(model)
        if model_sha != MODEL_SHA256:
            raise RuntimeError("Model SHA-256 differs from the pinned Japanese Parakeet INT8 model.")
        identity = {"schema": 1, "source_sha256": digest(source), "model_sha256": model_sha,
                    "tokens_sha256": digest(tokens), "sherpa_onnx_version": sherpa_onnx.__version__,
                    "model": "Parakeet Japanese CTC INT8", "sample_rate": SAMPLE_RATE,
                    "channels": 1, "chunk_samples": CHUNK_SAMPLES, "threads": 2,
                    "decoding_method": "greedy_search", "language": "ja"}
        job_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        manifest_path = output / "manifest.json"
        existing = None
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict) or existing.get("identity") != identity:
                raise RuntimeError("Source/model/configuration changed; use a new output directory.")
        emit(output, "preparing", job_key=job_key)
        atomic_json(output / "result.json", {"status": "processing", "job_key": job_key, "identity": identity})
        try:
            wav, total_frames = normalized_audio(source, output, identity["source_sha256"], sf)
            pieces = intervals(total_frames)
            if existing is not None and (existing.get("intervals") != pieces or existing.get("total_frames") != total_frames):
                raise RuntimeError("Checkpoint intervals do not cover this exact decoded recording.")
            manifest = {"identity": identity, "job_key": job_key, "source_name": args.source_name or source.name,
                        "source_id": args.source_id, "total_frames": total_frames,
                        "duration_seconds": total_frames / SAMPLE_RATE, "intervals": pieces}
            if existing is None:
                atomic_json(manifest_path, manifest)
            emit(output, "prepared", chunks=len(pieces), duration_seconds=total_frames / SAMPLE_RATE)
            recognizer = None
            segments = []
            with sf.SoundFile(str(wav)) as audio:
                for interval in pieces:
                    index = interval["index"]
                    cached = load_checkpoint(output, interval, job_key)
                    if cached is not None:
                        metadata, text = cached
                        segments.append({**metadata, "text": text})
                        emit(output, "reused", index=index, status=metadata["status"], completed_seconds=interval["end"])
                        continue
                    if recognizer is None:
                        emit(output, "loading_model")
                        recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                            model=str(model), tokens=str(tokens), num_threads=2,
                            sample_rate=SAMPLE_RATE, feature_dim=80, decoding_method="greedy_search",
                            debug=False, provider="cpu")
                        emit(output, "model_loaded", peak_rss_bytes=peak_rss_bytes())
                    audio.seek(interval["start_frame"])
                    samples = audio.read(interval["end_frame"] - interval["start_frame"], dtype="float32", always_2d=False)
                    if len(samples) != interval["end_frame"] - interval["start_frame"]:
                        raise RuntimeError(f"Decoded audio range {index} is incomplete.")
                    emit(output, "processing", index=index, start=interval["start"], end=interval["end"])
                    text, status, attempts, elapsed = decode(recognizer, samples, output, index)
                    stem = f"chunk-{index:05d}"
                    text_path = output / (stem + ".txt")
                    atomic_text(text_path, text)
                    metadata = {**interval, "job_key": job_key, "status": status, "attempts": attempts,
                                "text_sha256": digest(text_path), "processed_at_utc": utc_now(),
                                "wall_seconds": round(elapsed, 3), "peak_rss_bytes": peak_rss_bytes()}
                    atomic_json(output / (stem + ".json"), metadata)
                    segments.append({**metadata, "text": text})
                    emit(output, "completed", index=index, status=status, completed_seconds=interval["end"],
                         total_seconds=total_frames / SAMPLE_RATE, wall_seconds=metadata["wall_seconds"],
                         peak_rss_bytes=metadata["peak_rss_bytes"])
            if digest(source) != identity["source_sha256"]:
                raise RuntimeError("Original audio changed during processing; final completion was refused.")
            # Revalidate every checkpoint immediately before publishing the full text.
            for interval in pieces:
                cached = load_checkpoint(output, interval, job_key)
                if cached is None or cached[1] != segments[interval["index"]]["text"]:
                    raise RuntimeError("A checkpoint changed before final assembly.")
            processed = utc_now()
            header = (f"{manifest['source_name']}\n元ファイルID: {args.source_id or ''}\n"
                      f"処理日時 (UTC): {processed}\nモデル: Parakeet Japanese CTC INT8\n"
                      f"録音長: {timestamp(total_frames / SAMPLE_RATE)}\n\n")
            body = "\n\n".join(
                f"[{timestamp(segment['start'])}–{timestamp(segment['end'])}]"
                + (" [uncertain_empty]" if segment["status"] == "uncertain_empty" else "")
                + "\n" + segment["text"] for segment in segments)
            transcript = output / "raw_transcript.txt"
            atomic_text(transcript, header + body + "\n")
            uncertain = [segment["index"] for segment in segments if segment["status"] == "uncertain_empty"]
            result = {"status": "local_completed_with_uncertainty" if uncertain else "local_completed",
                      "job_key": job_key, "identity": identity, "source_name": manifest["source_name"],
                      "source_id": args.source_id, "processed_at_utc": processed,
                      "duration_seconds": total_frames / SAMPLE_RATE, "total_frames": total_frames,
                      "segments": segments, "uncertain_empty_indices": uncertain,
                      "wall_seconds_this_run": round(time.monotonic() - started, 3),
                      "inference_wall_seconds": round(sum(segment["wall_seconds"] for segment in segments), 3),
                      "peak_rss_bytes": max([peak_rss_bytes()] + [segment["peak_rss_bytes"] for segment in segments]),
                      "raw_transcript": {"filename": transcript.name, "sha256": digest(transcript),
                                         "size": transcript.stat().st_size}}
            atomic_json(output / "result.json", result)
            emit(output, result["status"], chunks=len(segments), uncertain_empty_indices=uncertain,
                 wall_seconds_this_run=result["wall_seconds_this_run"], peak_rss_bytes=result["peak_rss_bytes"])
            return result
        except Exception as error:
            try:
                atomic_json(output / "result.json", {"status": "failed", "job_key": job_key,
                            "error_type": type(error).__name__, "identity": identity})
                emit(output, "failed", error_type=type(error).__name__)
            except OSError:
                pass
            raise


def main():
    os.umask(0o077)
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["OPENBLAS_NUM_THREADS"] = "2"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-name")
    parser.add_argument("--source-id")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
