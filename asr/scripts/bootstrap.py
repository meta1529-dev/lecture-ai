#!/usr/bin/env python3
"""Restore the pinned CPU runtime. Matching cached files are never downloaded again."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PARAKEET_REPO = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8"
PARAKEET_REVISION = "bef18eb066808c90bd0f5df5be685767b0732de8"
QWEN_REVISION = "7278e1e70fe206f11671096ffdd38061171dd6e5"
ENGINE_REVISION = "924694251d9e0f18e5d86bbd06aa3ab5f870002d"
DEPENDENCIES = {"sherpa-onnx": "1.13.8", "soundfile": "0.14.0", "numpy": "2.3.5"}
PARAKEET_FILES = {
    "model.int8.onnx": (655542604, "3addd00ef5bd1742078389e540b77394e4a508bdf2f4c9ad1b4a76d93e76598e"),
    "tokens.txt": (28557, "732f64c53909f2620c713f4106b487d92e6f54a6915b3cd3d1dbd32f9f4f392a"),
}
QWEN_FILES = {
    "config.json": (6194, "2e74a751548b8ad7d7526d29365ad8144c345d8b412b1152d25dc6698452712f"),
    "generation_config.json": (142, "1da527824d81e07118facff437e03f2e24a23311e3bdeb2368973fe77e5f275c"),
    "model.safetensors.index.json": (64821, "f994739fe38e5210b9e3e8ce6c6307315e2ceac3cb630e7b7414d69dce520f60"),
    "merges.txt": (1671853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
    "model-00001-of-00002.safetensors": (4220320824, "a4cd1f1a04d90b757dc7f7dd26254e69a013b19e80efe590a83c6a3bde8608d6"),
    "model-00002-of-00002.safetensors": (478200688, "6e0b9d9e09e2e0238e7ef3cc8a484ab387e91b90f1900bedf88bc92d7929ccfc"),
}
BLAS_PACKAGES = {
    "runtime.deb": ("libopenblas0-pthread", 6709044, "4f7e4561dbfa9286c671c91350204fbda7afad675500e59cd4a56f84604844f8"),
    "dev.deb": ("libopenblas-pthread-dev", 4971360, "71325c822115f962bfe85c529f3b839e67f0fb6127f0918ebc2bd6ea3d527950"),
}
BLAS_EXTRACTED = {
    "usr/lib/x86_64-linux-gnu/openblas-pthread/libopenblasp-r0.3.21.so": "234bd1960ceeed3c44b275ba10583407ed7b9760d45d33d743420f70c46a0745",
    "usr/include/x86_64-linux-gnu/openblas-pthread/cblas.h": "4ca05f518fa1a9e526da58ae195c9491b191d96337f6b910c9d0330eec4aa68a",
    "usr/include/x86_64-linux-gnu/openblas-pthread/openblas_config.h": "5957738e5f2171dc9e8771ced2445ec921d86a3130237b6bca679751cd3901af",
}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def valid(path, size, checksum):
    return path.is_file() and path.stat().st_size == size and sha256(path) == checksum


def event(name, **values):
    print(json.dumps({"event": name, **values}), flush=True)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def download(url, target, size, checksum):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if valid(target, size, checksum):
        event("reused", file=target.name)
        return
    partial = target.with_name(target.name + ".part")
    if valid(partial, size, checksum):
        partial.replace(target)
        event("reused", file=target.name)
        return
    if partial.exists() and partial.stat().st_size >= size:
        partial.unlink()
    event("downloading", file=target.name, bytes=size)
    subprocess.run(["curl", "-fLsS", "--retry", "3", "--retry-delay", "2", "--max-time", "1800",
                    "-C", "-", url, "-o", str(partial)], check=True)
    if not valid(partial, size, checksum):
        partial.unlink(missing_ok=True)
        raise RuntimeError("Download checksum/size verification failed: " + target.name)
    with partial.open("rb") as stream:
        os.fsync(stream.fileno())
    partial.replace(target)
    event("verified", file=target.name)


def python_environment():
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    inspect = "import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in " + repr(list(DEPENDENCIES)) + "}))"
    try:
        installed = json.loads(subprocess.check_output([str(python), "-c", inspect], stderr=subprocess.DEVNULL))
    except (subprocess.SubprocessError, ValueError):
        installed = {}
    if installed != DEPENDENCIES:
        subprocess.run([str(python), "-m", "pip", "install", *[name + "==" + version for name, version in DEPENDENCIES.items()]], check=True)
    else:
        event("reused", component="python-dependencies")
    return python


def model_files(repo, revision, destination, files):
    for name, (size, checksum) in files.items():
        download(f"https://huggingface.co/{repo}/resolve/{revision}/{name}", destination / name, size, checksum)


def openblas():
    directory = RUNTIME / "openblas"
    for name, (package, size, checksum) in BLAS_PACKAGES.items():
        download(f"https://deb.debian.org/debian/pool/main/o/openblas/{package}_0.3.21+ds-4_amd64.deb",
                 directory / name, size, checksum)
    if not all((directory / name).is_file() and sha256(directory / name) == checksum for name, checksum in BLAS_EXTRACTED.items()):
        for name in BLAS_PACKAGES:
            subprocess.run(["dpkg-deb", "-x", str(directory / name), str(directory)], check=True)
    if not all((directory / name).is_file() and sha256(directory / name) == checksum for name, checksum in BLAS_EXTRACTED.items()):
        raise RuntimeError("Extracted OpenBLAS integrity check failed.")
    return directory


def qwen_engine(python, blas):
    source = RUNTIME / "qwen-asr"
    if not source.exists():
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "https://github.com/antirez/qwen-asr.git", str(source)], check=True)
    if not (source / ".git").is_dir():
        raise RuntimeError("Existing engine directory is not a Git checkout.")
    available = subprocess.run(["git", "-C", str(source), "cat-file", "-e", ENGINE_REVISION + "^{commit}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not available:
        subprocess.run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", ENGINE_REVISION], check=True)
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip() if (source / ".git/HEAD").is_file() else ""
    if head != ENGINE_REVISION or not (source / "qwen_asr.c").is_file():
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", ENGINE_REVISION], check=True)
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", ENGINE_REVISION, "--"], check=True, stdout=subprocess.DEVNULL)
    library = blas / "usr/lib/x86_64-linux-gnu/openblas-pthread"
    include = blas / "usr/include/x86_64-linux-gnu/openblas-pthread"
    flags = f"-Wall -Wextra -O3 -march=native -ffast-math -DUSE_BLAS -DUSE_OPENBLAS -I{include}"
    signature = {"engine_revision": ENGINE_REVISION, "worker_source_sha256": sha256(ROOT / "scripts/qwen_batch.c"),
                 "build_script_sha256": sha256(ROOT / "scripts/build_qwen_worker.py"), "cflags": flags,
                 "openblas": "0.3.21+ds-4"}
    # Exclude dynamic cpu MHz from the cache signature; native flags are stable per flag set.
    signature["cpu_info_sha256"] = hashlib.sha256("\n".join(line for line in Path("/proc/cpuinfo").read_text().splitlines()
                                             if line.startswith(("model name", "flags"))).encode()).hexdigest()
    binary, worker = source / "qwen_asr", RUNTIME / "qwen_asr_batch"
    receipt = RUNTIME / "qwen-build.json"
    try:
        saved = json.loads(receipt.read_text())
        if saved.get("signature") == signature and all(path.is_file() and sha256(path) == saved.get(path.name) for path in (binary, worker)):
            event("reused", component="qwen-cpu-binaries")
            return
    except (OSError, ValueError):
        pass
    subprocess.run(["make", "clean-cpu"], cwd=source, check=True)
    subprocess.run(["make", "-j2", "qwen_asr", "CFLAGS=" + flags,
                    f"LDFLAGS=-L{library} -Wl,-rpath,{library} -lopenblas -lm -lpthread"], cwd=source, check=True)
    subprocess.run([str(python), str(ROOT / "scripts/build_qwen_worker.py")], check=True)
    atomic_json(receipt, {"signature": signature, binary.name: sha256(binary), worker.name: sha256(worker)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-qwen", action="store_true", help="Also restore the pinned Qwen 1.7B secondary model and C worker.")
    args = parser.parse_args()
    os.umask(0o077)
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    tools = ["curl", "ffmpeg"] + (["git", "gcc", "make", "dpkg-deb"] if args.with_qwen else [])
    missing = [name for name in tools if not shutil.which(name)]
    if missing:
        raise RuntimeError("Install required system tools first: " + ", ".join(missing))
    if args.with_qwen and (platform.system() != "Linux" or platform.machine() != "x86_64"):
        raise RuntimeError("The pinned rootless OpenBLAS packages require Linux x86_64.")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    python = python_environment()
    model_files(PARAKEET_REPO, PARAKEET_REVISION, RUNTIME / "models/parakeet-ja", PARAKEET_FILES)
    if args.with_qwen:
        destination = RUNTIME / "models/Qwen3-ASR-1.7B"
        model_files("Qwen/Qwen3-ASR-1.7B", QWEN_REVISION, destination, QWEN_FILES)
        atomic_json(destination / "download-manifest.json", {"model_id": "Qwen/Qwen3-ASR-1.7B", "revision": QWEN_REVISION,
                    "files": [{"file": name, "size_bytes": size, "sha256": checksum} for name, (size, checksum) in QWEN_FILES.items()]})
        qwen_engine(python, openblas())
    event("ready", primary="Parakeet Japanese CTC INT8", qwen=args.with_qwen)


if __name__ == "__main__":
    main()
