from pathlib import Path
import argparse,datetime,hashlib,json,shutil,subprocess,re,sys
parser=argparse.ArgumentParser(description='Assemble locally reviewed private transcription artifacts.')
parser.add_argument('--job-dir',type=Path,required=True)
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];JOB=args.job_dir.expanduser().resolve();WORK=JOB/'asr';OUT=JOB/'review';OUT.mkdir(exist_ok=True)
job=json.loads((JOB/'job.json').read_text(encoding='utf-8'))
subprocess.run([sys.executable,str(ROOT/'scripts/review_batches.py'),'--job-dir',str(JOB),'secondary'],check=True)
result=json.loads((WORK/'result.json').read_text());assert result['status'] in ('local_completed','local_completed_with_uncertainty')
queue=json.loads((JOB/'recheck_requests.json').read_text());assert queue['sealed']
completed=json.loads((JOB/'qwen-recheck/complete.json').read_text());assert set(completed['requested'])==set(queue['indices'])
entries={}
for p in sorted((JOB/'reviewed').glob('batch-*.json')):
 for e in json.loads(p.read_text())['segments']:
  assert e['index'] not in entries;entries[e['index']]=e
segments=result['segments'];assert set(entries)==set(range(len(segments)))
secondary={i for i,e in entries.items() if 'secondary_model' in e};assert secondary==set(queue['indices'])
notes=[];last=0
for i,raw in enumerate(segments):
 e=entries[i];assert raw['index']==i and raw['start']==last and raw['end']>last;last=raw['end']
 assert e['raw_sha256']==hashlib.sha256(raw['text'].encode()).hexdigest()
 assert isinstance(e['corrected_text'],str) and (not raw['text'].strip() or e['corrected_text'].strip())
 for n in e['uncertainties']:
  assert raw['start']<=n['start']<n['end']<=raw['end'];notes.append(n)
assert abs(last-result['duration_seconds'])<1e-6
source_path=Path(job.get('source_path','source.m4a'))
if not source_path.is_absolute():source_path=JOB/source_path
with source_path.open('rb') as source_stream:
 assert hashlib.file_digest(source_stream,'sha256').hexdigest()==result['identity']['source_sha256']
shutil.copyfile(WORK/'raw_transcript.txt',OUT/'raw_transcript.txt')
def stamp(v):
 ms=round(v*1000);s,r=divmod(ms,1000);return f'{s//3600:02d}:{s//60%60:02d}:{s%60:02d}.{r:03d}'
title=job['title'];references='、'.join(job.get('references',[]))
header=(f'# {title}\n\n## 資料照合による校正版文字起こし\n\n'
 f'元音声: {job["source_name"]}  \n録音時間: {stamp(last)}  \n処理日時 (UTC): {result["processed_at_utc"]}  \n'
 f'一次認識: NVIDIA Parakeet TDT-CTC 0.6B Japanese（sherpa-onnx CTC INT8）  \n'
 f'追加認識: Qwen3-ASR-1.7B（選択{len(secondary)}区間）  \n照合資料: {references}  \n'
 f'元音声Drive: {job["source_url"]}\n\n'
 '全区間を時刻付きで収録。資料に対応する専門用語・記号・単位を校正し、資料にあるだけの発話は追加していません。'
 '未確定の語句・数値、資料との食い違いは「要確認」とし、詳細を uncertain_sections.md に記録しています。\n\n')
body='\n\n'.join(f'**[{stamp(raw["start"])}–{stamp(raw["end"])}]** {entries[i]["corrected_text"]}' for i,raw in enumerate(segments))+'\n'
(OUT/'corrected_transcript.md').write_text(header+body)
plain=(re.sub(r'^#{1,6} +','',header,flags=re.M).replace('  \n','\n')+body.replace('**',''))
(OUT/'corrected_transcript.txt').write_text(plain)
uncertain=f'# {title} — 要確認箇所\n\n元音声: {job["source_name"]}\n\n| 時刻 | 理由 |\n|---|---|\n'
uncertain+=''.join(f'| {stamp(n["start"])}–{stamp(n["end"])} | {n["reason"].replace(chr(124),"／")} |\n' for n in sorted(notes,key=lambda n:(n['start'],n['end']))) or '| なし | — |\n'
(OUT/'uncertain_sections.md').write_text(uncertain)
files={}
for p in OUT.iterdir():
 if p.name not in ('raw_transcript.txt','corrected_transcript.md','corrected_transcript.txt','uncertain_sections.md'):continue
 data=p.read_bytes();data.decode('utf-8');files[p.name]={'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data)}
report={'all_intervals_present':len(segments),'duration_seconds':last,'raw_hashes_match':True,'source_hash_match':True,'utf8_valid':True,'secondary_reviewed':sorted(secondary),'uncertainty_notes':len(notes),'files':files,'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
(JOB/'local_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
job.update(status='ready_to_upload',local_verification=report);(JOB/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('secondary_reviewed','files')},ensure_ascii=False))
