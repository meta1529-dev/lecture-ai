#define _POSIX_C_SOURCE 200809L
#include "qwen_asr.h"
#include "qwen_asr_kernels.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int save(const char *path,const char *text){char tmp[4096];if(snprintf(tmp,sizeof(tmp),"%s.part.XXXXXX",path)>=(int)sizeof(tmp))return -1;int fd=mkstemp(tmp);if(fd<0)return -1;FILE*f=fdopen(fd,"wb");if(!f){close(fd);unlink(tmp);return -1;}size_t n=strlen(text);int fail=fwrite(text,1,n,f)!=n;if(n==0||text[n-1]!='\n')fail|=fputc('\n',f)==EOF;fail|=fflush(f)!=0;fail|=fsync(fd)!=0;fail|=fclose(f)!=0;if(fail){unlink(tmp);return -1;}return rename(tmp,path);}
int main(int argc,char **argv){if(argc!=2)return 2;qwen_verbose=0;qwen_set_threads(2);qwen_ctx_t *ctx=qwen_load(argv[1]);if(!ctx)return 3;ctx->segment_sec=0;ctx->skip_silence=0;if(qwen_set_force_language(ctx,"Japanese")){qwen_free(ctx);return 4;}fprintf(stderr,"{\"event\":\"model_ready\"}\n");fflush(stderr);char *line=NULL;size_t capacity=0;while(getline(&line,&capacity,stdin)>0){line[strcspn(line,"\r\n")]=0;char *id=strtok(line,"\t"),*wav=strtok(NULL,"\t"),*out=strtok(NULL,"\t");if(!id||!wav||!out){puts("{\"status\":\"invalid_job\"}");fflush(stdout);continue;}double start=now();char *text=qwen_transcribe(ctx,wav);int ok=text&&*text&&save(out,text)==0;free(text);printf("{\"id\":%d,\"status\":\"%s\",\"wall_seconds\":%.3f}\n",atoi(id),ok?"ok":"empty_or_failed",now()-start);fflush(stdout);}free(line);qwen_free(ctx);return 0;}
