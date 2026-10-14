#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int rmdir(const char *);
int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
int main(void) {
  char root[]="/tmp/anvil-profile-identity-XXXXXX";
  assert(mkdtemp(root));
  int dir=open(root,PROFILE_DIRECTORY),w,h,maximized;assert(dir>=0);
  assert(profile_write_state(dir,"com.test","existing",768,600,1));
  assert(profile_read_state(dir,"com.test","default",&w,&h,&maximized)==1);
  assert(w==768&&h==600&&maximized);
  assert(profile_read(dir,"com.test","r-other",&w,&h)==-1);
  assert(profile_write(dir,"com.test","default",960,700));
  assert(profile_read(dir,"com.test","default",&w,&h)==1&&w==960&&h==700);
  assert(profile_read(dir,"com.test","existing",&w,&h)==-1);
  assert(!unlinkat(dir,"com.test",0));
  assert(profile_write(dir,"com.test","r-managed",800,600));
  assert(profile_read(dir,"com.test","default",&w,&h)==-1);
  assert(!profile_write(dir,"com.test","default",960,700));
  assert(profile_read(dir,"com.test","r-managed",&w,&h)==1&&w==800);
  assert(!unlinkat(dir,"com.test",0));close(dir);assert(!rmdir(root));
  return 0;
}
