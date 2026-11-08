from pathlib import Path
import argparse,hashlib,json,os,selectors,subprocess,time
import soundfile as sf
parser=argparse.ArgumentParser(description='Run queued Qwen secondary recognition for a private job.')
parser.add_argument('--job-dir',type=Path,required=True)
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];JOB=args.job_dir.expanduser().resolve();WORK=JOB/'asr';OUT=JOB/'qwen-recheck';OUT.mkdir(exist_ok=True)
os.umask(0o077);os.environ.update(OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2')
def atomic(p,value):
 tmp=p.with_suffix(p.suffix+'.part');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def log(**kw):print(json.dumps(kw,ensure_ascii=False),flush=True)
while not (WORK/'result.json').exists() or json.loads((WORK/'result.json').read_text()).get('status') not in ('local_completed','local_completed_with_uncertainty'):time.sleep(5)
# Wait until the primary process has actually released its model, then load the larger model.
while (JOB/'primary.pid').exists():
 pid=(JOB/'primary.pid').read_text().strip();p=Path('/proc')/pid/'stat'
 if not p.exists() or p.read_text().split(') ')[-1][0]=='Z':break
 time.sleep(5)
while not (ROOT/'.runtime/models/Qwen3-ASR-1.7B/download-manifest.json').exists():time.sleep(5)
manifest=json.loads((WORK/'manifest.json').read_text());pieces={v['index']:v for v in manifest['intervals']};worker=None;stderr=None;done=set();unresolved=set()
def valid(i):
 p=OUT/f'chunk-{i:05d}.txt';m=p.with_suffix('.json')
 if not p.exists() or not m.exists():return False
 s=json.loads(m.read_text());return s.get('status')=='ok' and s.get('interval')==pieces[i] and s.get('source_sha256')==manifest['identity']['source_sha256'] and s.get('text_sha256')==hashlib.sha256(p.read_bytes()).hexdigest()
try:
 while True:
  queue=json.loads((JOB/'recheck_requests.json').read_text());wanted=sorted(set(queue['indices']));pending=[i for i in wanted if i not in done]
  if not pending:
   if queue.get('sealed'):
    atomic(OUT/'complete.json',{'requested':wanted,'completed':sorted(done-unresolved),'unresolved':sorted(unresolved)});break
   time.sleep(5);continue
  for i in pending:
   if valid(i):done.add(i);log(event='reused',index=i);continue
   if worker is None:
    stderr=(OUT/'worker.log').open('a');worker=subprocess.Popen([str(ROOT/'.runtime/qwen_asr_batch'),str(ROOT/'.runtime/models/Qwen3-ASR-1.7B')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1)
   interval=pieces[i];wav=OUT/f'chunk-{i:05d}.wav';txt=wav.with_suffix('.txt')
   with sf.SoundFile(str(WORK/'audio16k.wav')) as audio:
    audio.seek(interval['start_frame']);samples=audio.read(interval['end_frame']-interval['start_frame'],dtype='float32')
   sf.write(wav,samples,16000,subtype='PCM_16');log(event='processing',index=i)
   started=time.monotonic();success=False
   for attempt in range(3):
    worker.stdin.write(f'{i}\t{wav}\t{txt}\n');worker.stdin.flush()
    sel=selectors.DefaultSelector();sel.register(worker.stdout,selectors.EVENT_READ)
    if not sel.select(300):raise RuntimeError(f'Qwen timeout at {i}; checkpoints kept')
    line=worker.stdout.readline();sel.close()
    if not line:raise RuntimeError('Qwen worker stopped; checkpoints kept')
    r=json.loads(line)
    if r.get('status')=='ok' and txt.exists() and any(c.isalnum() for c in txt.read_text()):success=True;break
   if success:
    atomic(txt.with_suffix('.json'),{'status':'ok','model':'Qwen/Qwen3-ASR-1.7B','model_revision':'7278e1e70fe206f11671096ffdd38061171dd6e5','interval':interval,'source_sha256':manifest['identity']['source_sha256'],'text_sha256':hashlib.sha256(txt.read_bytes()).hexdigest(),'wall_seconds':round(time.monotonic()-started,3)});done.add(i);log(event='completed',index=i)
   else:
    unresolved.add(i);done.add(i);atomic(txt.with_suffix('.json'),{'status':'unresolved','interval':interval});log(event='unresolved',index=i)
   wav.unlink(missing_ok=True)
finally:
 if worker:
  worker.stdin.close()
  try:worker.wait(timeout=30)
  except subprocess.TimeoutExpired:worker.kill();worker.wait()
 if stderr:stderr.close()
