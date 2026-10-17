#include "../native/bridge/bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int rmdir(const char *);
int __android_log_print(int l,const char*t,const char*f,...){(void)l;(void)t;(void)f;return 0;}
int main(void){
  assert(keymap_number("65535",65535)==65535);
  assert(keymap_number("4294967297",64)==-1);
  assert(keymap_number("-1",767)==-1);
  struct keymap_model m={.selected=-1},loaded;
  assert(keymap_add(&m,400,300,801,601));assert(!keymap_valid(&m,1));
  assert(m.points[0].nx==32768&&m.points[0].ny==32768);
  assert(keymap_assign(&m,30));assert(keymap_valid(&m,1));
  assert(keymap_add(&m,800,600,801,601));assert(!keymap_assign(&m,30));assert(m.error==1);
  assert(keymap_assign(&m,57));m.points[1].hold=1;
  struct mapping_binding bindings[2];
  assert(keymap_bindings(&m, bindings, 2) == 2);
  assert(bindings[0].key == 30 && bindings[0].nx == m.points[0].nx &&
         bindings[1].key == 57 && bindings[1].hold == 1);
  char path[]="/tmp/anvil-keymap-store-XXXXXX";assert(mkdtemp(path));
  int dir=open(path,PROFILE_DIRECTORY|PROFILE_NOFOLLOW);assert(dir>=0);
  assert(keymap_save(dir,"com.game.test","r-one",&m));
  assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1);
  assert(loaded.count==2&&loaded.points[1].key==57&&loaded.points[1].hold==1&&loaded.points[1].nx==65535);
  assert(keymap_load(dir,"com.game.test","r-two",&loaded)==-1);
  loaded.selected=1;keymap_remove(&loaded);assert(loaded.count==1);assert(keymap_save(dir,"com.game.test","r-one",&loaded));
  assert(!unlinkat(dir,"com.game.test",0));
  int legacy=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(legacy>=0);
  const char *v1="ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100 200 1\n";
  assert(write(legacy,v1,strlen(v1))==(long)strlen(v1));close(legacy);
  assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1&&!loaded.points[0].kind);
  m.count=64;
  for(int i=0;i<64;++i)m.points[i]=(struct keymap_point){.key=100+i,.nx=65535,.ny=65535,.kind=KEYMAP_SWIPE,.ex=65535,.ey=65535,.duration=5000,.repeats=20};
  assert(keymap_valid(&m,1));
  m.selected=1;m.points[0].kind=KEYMAP_DPAD;m.points[0].key=17;
  assert(!keymap_assign(&m,30));m.points[0].kind=KEYMAP_SWIPE;m.points[0].key=100;
  assert(!keymap_bindings(&m,bindings,2));
  assert(keymap_save(dir,"com.game.test","r-one",&m));
  assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1&&loaded.count==64&&loaded.points[63].duration==5000);
  assert(!unlinkat(dir,"com.game.test",0));
  const char *invalid[]={
    "ANVIL_KEYMAP 3 r-one com.game.test 0\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 1\n30 0 0 0 2 0 0 250 3 extra\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 1\n30 0 0 0 2 0 0 -1 3\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 1\n30 0 0 0 7 0 0 250 3\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 1\n30 0 0 0 5 0 0 5000 20\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 2\n17 0 0 0 1 0 0 250 3\n30 0 0 0 0 0 0 0 0\n"
  };
  for(unsigned i=0;i<sizeof(invalid)/sizeof(*invalid);++i) {
    int bad=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(bad>=0);
    assert(write(bad,invalid[i],strlen(invalid[i]))==(long)strlen(invalid[i]));close(bad);
    assert(keymap_load(dir,"com.game.test","r-one",&loaded)==-1);
    assert(!unlinkat(dir,"com.game.test",0));
  }
  int fd=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(fd>=0);assert(write(fd,"corrupt",7)==7);close(fd);
  assert(!keymap_save(dir,"com.game.test","r-one",&m));assert(keymap_load(dir,"com.game.test","r-one",&loaded)==-1);
  assert(!unlinkat(dir,"com.game.test",0));close(dir);assert(!rmdir(path));
}
