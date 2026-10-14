static char profile_root[128];
#define PROFILE_ROOT profile_root
#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int rmdir(const char *);
int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
int main(void) {
  snprintf(profile_root,sizeof(profile_root),"/tmp/anvil-resize-mode-XXXXXX");
  assert(mkdtemp(profile_root));
  struct object s={.app=1,.app_id="com.shg.sg451",.fit_w=721,.fit_h=633,
    .resize={.width=721,.height=633,.dirty=1}},other=s;
  s.next=&other;objects=&s;
  resize_mode_load(s.app_id);
  assert(resize_mode_fixed(s.app_id));
  resize_mode_set(s.app_id,1);
  assert(!s.resize.width&&!other.resize.width&&!s.resize.dirty);
  resize_mode_set(s.app_id,0);
  assert(s.resize.width==721&&other.resize.height==633);
  resize_mode_poll();
  assert(!resize_modes->dirty);
  /* Explicit layout choice overrides the compatibility default on restart. */
  resize_modes->loaded=0;resize_modes->fixed=1;
  resize_mode_load(s.app_id);
  assert(!resize_mode_fixed(s.app_id));
  resize_mode_set(s.app_id,1);resize_mode_poll();
  resize_modes->loaded=0;resize_modes->fixed=0;
  resize_mode_load(s.app_id);assert(resize_mode_fixed(s.app_id));
  resize_mode_set("com.test.app",1);resize_mode_poll();
  struct resize_mode_entry *normal=resize_mode_find("com.test.app",0);
  normal->loaded=0;normal->fixed=0;
  resize_mode_load(normal->package);assert(resize_mode_fixed(normal->package));
  assert(!resize_mode_find("../invalid",1));
  int dir=resize_mode_dir();assert(dir>=0);
  assert(!unlinkat(dir,s.app_id,0)&&!unlinkat(dir,"com.test.app",0));close(dir);
  char path[256];snprintf(path,sizeof(path),"%s/resize-modes",profile_root);
  assert(!rmdir(path)&&!rmdir(profile_root));
  return 0;
}
