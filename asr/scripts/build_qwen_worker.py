from pathlib import Path
import subprocess
r=Path(__file__).resolve().parents[1];src=r/'.runtime/qwen-asr';lib=r/'.runtime/openblas/usr/lib/x86_64-linux-gnu/openblas-pthread';inc=r/'.runtime/openblas/usr/include/x86_64-linux-gnu/openblas-pthread'
objects=[str(p) for p in src.glob('*.o') if p.name!='main.o']
assert len(objects)==10,objects
subprocess.run(['gcc','-O3','-march=native','-ffast-math','-I'+str(src),'-I'+str(inc),str(r/'scripts/qwen_batch.c'),*objects,'-L'+str(lib),'-Wl,-rpath,'+str(lib),'-lopenblas','-lm','-lpthread','-o',str(r/'.runtime/qwen_asr_batch')],check=True)
print('Persistent Qwen1.7B CPU worker built')
