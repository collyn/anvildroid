static char profile_root[128];
#define PROFILE_ROOT profile_root
#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int setenv(const char *, const char *, int);
extern int rmdir(const char *);
int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static void settle(void) {for(int n=0;n<12;++n)profile_poll();}
int main(void) {
  snprintf(profile_root,sizeof(profile_root),"/tmp/anvil-fit-profile-XXXXXX");
  assert(mkdtemp(profile_root)&&!setenv("ANVILDROID_RUNTIME_ID","r-unit",1));
  resize_nvidia_backend=1;hwc_scale_120=120;scale_has_viewporter=1;
  struct object viewport={.width=1984,.height=2164};
  struct game_fit_state fit={0};
  struct object s={.app=1,.app_id="com.fitted",.has_bounds=1,.viewport=&viewport,.fit=&fit,
    .content_width=448,.content_height=701,.fit_w=465,.fit_h=701,
    .fit_delivered_w=465,.fit_delivered_h=701,.profile_loaded=1,.profile_dirty=1,
    .resize={.ready=1}};
  objects=&s;task_count=1;
  tasks[0]=(struct task){.id=12,.package="com.fitted",.width=448,.height=701};
  settle();
  int dir=open(profile_root,PROFILE_DIRECTORY),w=0,h=0;assert(dir>=0);
  assert(profile_read(dir,"com.fitted","r-unit",&w,&h)==1&&w==465&&h==701);
  /* A drag within the same aligned-width bucket preserves its new host size. */
  s.fit_w=s.fit_delivered_w=466;s.profile_dirty=1;settle();
  assert(profile_read(dir,"com.fitted","r-unit",&w,&h)==1&&w==466&&h==701);
  /* An old task snapshot cannot overwrite a newer, not-yet-delivered frame. */
  s.fit_w=700;s.profile_dirty=1;settle();
  assert(profile_read(dir,"com.fitted","r-unit",&w,&h)==1&&w==466);
  objects=0;for(int n=0;n<3;++n)profile_poll();
  struct object fresh={.app=1,.app_id="com.fitted",.has_bounds=1,.viewport=&viewport,.fit=&fit,
    .content_width=512,.content_height=704,.fit_w=512,.fit_h=704,
    .fit_delivered_w=512,.fit_delivered_h=704,.resize={.ready=1}};
  tasks[0].id=13;tasks[0].width=512;tasks[0].height=704;
  objects=&fresh;settle();
  assert(fresh.resize.width==448&&fresh.resize.height==701&&fresh.profile_restoring);
  assert(fresh.fit_w==448&&fresh.fit_h==701&&!fresh.preview_width);
  objects=0;
  assert(!unlinkat(dir,"com.fitted",0));close(dir);assert(!rmdir(profile_root));
  while(profile_seen){struct profile_task_seen *next=profile_seen->next;free(profile_seen);profile_seen=next;}
  return 0;
}
