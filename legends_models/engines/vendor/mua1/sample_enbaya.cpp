// Offline animation sampler. Loads the owner's XML2 DLL, never a game executable.
// RVAs are specific to SHA256 recorded by export_animations.py.
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <cstdint>
#include <cstring>
#include <string>
int main(int argc,char**argv) {
 if(argc!=5 && argc!=6){fprintf(stderr,"sampler dll_directory stream.bin output.bin frames [times-int64-ns.bin]\n");return 2;}
 SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
 SetDllDirectoryA(argv[1]);
 HMODULE h=LoadLibraryA((std::string(argv[1])+"/libIGSg.dll").c_str());
 if(!h){fprintf(stderr,"LoadLibrary %lu\n",GetLastError());return 3;}
 auto base=(unsigned char*)h;
 FILE*f=fopen(argv[2],"rb");if(!f)return 4;
 fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);
 std::vector<unsigned char> stream(size+256);fread(stream.data(),1,size,f);fclose(f);
 unsigned bones=*(unsigned*)(stream.data()+4),fps=*(unsigned*)(stream.data()+16);
 float duration=*(float*)(stream.data()+12);
 if(bones>256||fps>120||!fps)return 5;
 typedef void*(__cdecl* Fix)(void*);
 typedef unsigned(__cdecl* Size)(unsigned);
 typedef void*(__cdecl* Create)(void*,unsigned);
 typedef void(__thiscall* Bind)(void*,void*,bool);
 typedef bool(__thiscall* Sample)(void*,float*,float*,int64_t,int);
 void*s=((Fix)(base+0x6e7d0))(stream.data());
 std::vector<unsigned char> mem(((Size)(base+0x6d920))(bones)+256);
 void*ctx=((Create)(base+0x6d930))(mem.data(),bones);
 ((Bind)(base+0x6da50))(ctx,s,false);
 *(void**)(base+0x151a58)=ctx;
 unsigned char source[64]={};source[8]=1;source[9]=3;source[10]=1;source[11]=3;
 *(int*)(source+12)=1; // clamp
 *(int64_t*)(source+24)=-1;
 *(void**)(source+32)=s;
 int frames=atoi(argv[4]);if(frames<2)return 6;
 std::vector<int64_t> times;
 if(argc==6){
  FILE*tf=fopen(argv[5],"rb");if(!tf)return 9;
  times.resize(frames);size_t got=fread(times.data(),8,frames,tf);fclose(tf);
  if(got!=(size_t)frames)return 10;
 }
 f=fopen(argv[3],"wb");if(!f)return 7;
 fwrite(&bones,4,1,f);fwrite(&frames,4,1,f);fwrite(&fps,4,1,f);fwrite(&duration,4,1,f);
 for(int j=0;j<frames;j++){
  int64_t t=times.empty()?(int64_t)((double)duration*j/(frames-1)*1e9):times[j];
  for(unsigned i=0;i<bones;i++){
   float q[4]={},p[4]={};
   if(!((Sample)(base+0x45040))(source,q,p,t,i))return 8;
   fwrite(q,4,4,f);fwrite(p,4,3,f);
  }
 }
 fclose(f);printf("bones=%u fps=%u duration=%.6f samples=%d\n",bones,fps,duration,frames);
 return 0;
}

