#!/usr/bin/env python3
"""Apply explicit review edits and secondary decisions to a private job."""
from pathlib import Path
import argparse
import hashlib
import json

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--job-dir',type=Path,required=True)
commands=parser.add_subparsers(dest='mode',required=True)
show=commands.add_parser('show');show.add_argument('start',type=int);show.add_argument('count',type=int)
apply=commands.add_parser('apply');apply.add_argument('spec',type=Path)
commands.add_parser('secondary')
args=parser.parse_args()
ROOT=args.job_dir.expanduser().resolve();WORK=ROOT/'asr';NOTES=ROOT/'reviewed';NOTES.mkdir(exist_ok=True)

def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def read(i):
 p=WORK/f'chunk-{i:05d}.txt';return p.read_text(encoding='utf-8'),json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))
def stamp(s):
 n=int(s);return f'{n//3600:02d}:{n//60%60:02d}:{n%60:02d}'
if args.mode=='show':
 for i in range(args.start,args.start+args.count):
  if not (WORK/f'chunk-{i:05d}.json').exists():print('NOT_READY',i);break
  t,m=read(i);print(f"[{i} {stamp(m['start'])}–{stamp(m['end'])} {m['status']}] {t}")
elif args.mode=='apply':
 spec=json.loads(args.spec.read_text(encoding='utf-8'));entries=[]
 for i in range(spec['start'],spec['end']+1):
  t,m=read(i);c=t
  assert digest(t)==m['text_sha256']
  for e in spec.get('edits',{}).get(str(i),[]):
   old,new=e[:2];count=e[2] if len(e)>2 else 1
   if c.count(old)!=count:raise ValueError(f'chunk {i}: {old!r}: expected {count}, got {c.count(old)}')
   c=c.replace(old,new)
  notes=spec.get('uncertain',{}).get(str(i),[])
  if isinstance(notes,str):notes=[notes]
  if not any(ch.isalnum() for ch in t):notes=notes+['発話文字を認識できなかった区間。原音要確認。']
  if notes and '[要確認]' not in c:c+=' [要確認]'
  entries.append({'index':i,'raw_sha256':digest(t),'corrected_text':c,'uncertainties':[{'start':m['start'],'end':m['end'],'reason':s} for s in notes],'edits':spec.get('edits',{}).get(str(i),[])})
 job=json.loads((ROOT/'job.json').read_text(encoding='utf-8'))
 p=NOTES/f"batch-{spec['start']:05d}-{spec['end']:05d}.json";p.write_text(json.dumps({'references':job.get('references',[]),'segments':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print({'saved':str(p),'segments':len(entries),'edits':sum(len(e['edits']) for e in entries)})
elif args.mode=='secondary':
 decisions={}
 for p in ROOT.glob('qwen-decisions*.json'):
  for e in json.loads(p.read_text(encoding='utf-8'))['segments']:
   if e['index'] in decisions:raise ValueError('Overlapping secondary decisions')
   q=ROOT/f"qwen-recheck/chunk-{e['index']:05d}.txt";meta=json.loads(q.with_suffix('.json').read_text(encoding='utf-8'))
   assert meta['status']=='ok' and digest(q.read_text(encoding='utf-8'))==meta['text_sha256'];decisions[e['index']]=e
 count=0
 for p in sorted(NOTES.glob('batch-*.json')):
  batch=json.loads(p.read_text(encoding='utf-8'))
  for e in batch['segments']:
   i=e['index']
   if i not in decisions:continue
   d=decisions[i];_,m=read(i)
   e.setdefault('primary_corrected_text',e['corrected_text']);e.setdefault('primary_uncertainties',e['uncertainties'])
   e['corrected_text']=d['corrected_text'];e['uncertainties']=[{'start':m['start'],'end':m['end'],'reason':s} for s in d['uncertain_reasons']]
   if e['uncertainties'] and '[要確認]' not in e['corrected_text']:e['corrected_text']+=' [要確認]'
   e['secondary_model']='Qwen/Qwen3-ASR-1.7B';count+=1
  p.write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print({'secondary_reviewed':count})
