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
    "ANVIL_KEYMAP 5 r-one com.game.test 0\n",
    " ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100 200 1\n",
    "ANVIL_KEYMAP1 r-one com.game.test 1\n30 100 200 1\n",
    "ANVIL_KEYMAP 1\nr-one com.game.test 1\n30 100 200 1\n",
    "ANVIL_KEYMAP 1 r-one com.game.test 1\r\r\n30 100 200 1\n",
    "ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100 200 1\r\r\n",
    "ANVIL_KEYMAP 1 r-one com.game.test 1 30 100 200 1\n",
    "ANVIL_KEYMAP 1 r-one com.game.test 2\n30 100 200 1 31 100 200 1\n",
    "ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100\n200 1\n",
    "ANVIL_KEYMAP 3 r-one com.game.test 1\n30 100 200 0 0 0 0 50 1 0 1 0 0 0 0 0 0 0\n",
    "ANVIL_KEYMAP 3 r-one com.game.test 1\n30 100 200 0 0 0 0 50 1 0 1 0 0 0 0 0 0 0 0 99\n",
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
  m=(struct keymap_model){.count=4,.selected=-1};
  m.points[0]=(struct keymap_point){.key=30,.nx=10000,.ny=20000};
  keymap_set_kind(&m.points[0],KEYMAP_SWIPE);m.points[0].end_mode=KEYMAP_END_HOLD;
  m.points[1]=(struct keymap_point){.key=272,.nx=50000,.ny=40000};
  keymap_set_kind(&m.points[1],KEYMAP_BASIC);m.points[1].source=KEYMAP_SOURCE_MOUSE;
  m.points[2]=(struct keymap_point){.nx=32768,.ny=32768};
  keymap_set_kind(&m.points[2],KEYMAP_MOUSE_TOUCH);
  m.points[3]=(struct keymap_point){.key=34,.nx=42000,.ny=33000};
  keymap_set_kind(&m.points[3],KEYMAP_LOOK);m.points[3].activation=1;
  m.points[3].invert_y=1;m.points[3].sensitivity_x=250;m.points[3].sensitivity_y=175;
  m.points[3].suspend_key=56;
  m.points[1].end_mode=KEYMAP_END_HOLD;m.points[1].drag_shot=1;
  assert(keymap_save(dir,"com.game.test","r-one",&m));
  assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1);
  assert(!memcmp(m.points,loaded.points,sizeof(m.points)));
  m.points[3].activation=KEYMAP_CAMERA_AUTO;
  assert(keymap_save(dir,"com.game.test","r-one",&m));
  assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1);
  assert(!memcmp(m.points,loaded.points,sizeof(m.points)));
  int inspect=openat(dir,"com.game.test",PROFILE_NOFOLLOW);assert(inspect>=0);
  char header[16]={0};assert(read(inspect,header,14)==14);close(inspect);
  assert(!memcmp(header,"ANVIL_KEYMAP 4",14));
  assert(!unlinkat(dir,"com.game.test",0));
  /* All old versions retain zero extensions and loading never rewrites bytes. */
  const char *old_profiles[]={
    "ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100 200 1\n",
    "ANVIL_KEYMAP\t1 r-one com.game.test 1\r\n000000000000030 100 200 1\r\n",
    "ANVIL_KEYMAP 2 r-one com.game.test 1\n30 100 200 0 4 500 500 250 3\n",
    "ANVIL_KEYMAP 3 r-one com.game.test 1\n30 100 200 0 4 500 500 250 3 0 0 0 0 0 0 0 0 0 0\n"};
  for(unsigned v=0;v<sizeof(old_profiles)/sizeof(*old_profiles);++v) {
    const char *text=old_profiles[v];
    int fd=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(fd>=0);
    assert(write(fd,text,strlen(text))==(long)strlen(text));close(fd);
    assert(keymap_load(dir,"com.game.test","r-one",&loaded)==1);
    assert(!loaded.points[0].up_key&&!loaded.points[0].skill_mode&&!loaded.points[0].cancel_key);
    fd=openat(dir,"com.game.test",PROFILE_NOFOLLOW);char original[256]={0};
    assert(read(fd,original,sizeof(original))==(long)strlen(text));close(fd);
    assert(!strcmp(original,text));assert(!unlinkat(dir,"com.game.test",0));
  }
  char wire[KEYMAP_PROFILE_LIMIT+1];int wire_size=keymap_profile_format(wire,sizeof(wire),"com.game.test","r-one",&m);
  assert(wire_size>0&&keymap_profile_parse(wire,wire_size,"com.game.test","r-one",&loaded));
  wire[wire_size-1]=' ';assert(!keymap_profile_parse(wire,wire_size,"com.game.test","r-one",&loaded));
  wire[wire_size-1]='\n';wire[wire_size/2]=0;
  assert(!keymap_profile_parse(wire,wire_size,"com.game.test","r-one",&loaded));
  int oversized=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(oversized>=0);
  const char *empty="ANVIL_KEYMAP 3 r-one com.game.test 0\n";
  assert(write(oversized,empty,strlen(empty))==(long)strlen(empty));
  char padding[1024];memset(padding,' ',sizeof(padding));
  for(int i=0;i<17;++i)assert(write(oversized,padding,sizeof(padding))==(long)sizeof(padding));
  close(oversized);assert(keymap_load(dir,"com.game.test","r-one",&loaded)==-1);
  assert(!keymap_save(dir,"com.game.test","r-one",&m));
  assert(!unlinkat(dir,"com.game.test",0));
  int fd=openat(dir,"com.game.test",1|PROFILE_CREAT|PROFILE_EXCL,0600);assert(fd>=0);assert(write(fd,"corrupt",7)==7);close(fd);
  assert(!keymap_save(dir,"com.game.test","r-one",&m));assert(keymap_load(dir,"com.game.test","r-one",&loaded)==-1);
  assert(!unlinkat(dir,"com.game.test",0));close(dir);assert(!rmdir(path));
}
