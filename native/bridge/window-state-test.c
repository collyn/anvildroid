static char profile_root[128];
#define PROFILE_ROOT profile_root
#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int setenv(const char *,const char *,int);
extern int rmdir(const char *);
int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static struct proxy top_proxy,seat_proxy;
static int maximize_requests,restore_requests,move_requests;
static uint32_t version(struct proxy *p) {(void)p;return 1;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)a;(void)i;(void)v;
  if(p==&top_proxy) {
    if(op==9)++maximize_requests;
    if(op==10)++restore_requests;
    if(op==5)++move_requests;
  }
  return NULL;
}
static void settle(void) {for(int i=0;i<12;++i)profile_poll();}
int main(void) {
  snprintf(profile_root,sizeof(profile_root),"/tmp/anvil-window-state-XXXXXX");
  assert(mkdtemp(profile_root));assert(!setenv("ANVILDROID_RUNTIME_ID","r-test",1));
  real_marshal=record;get_version=version;
  struct object surface={.app=1,.app_id="com.test",.has_bounds=1,
    .content_width=800,.content_height=600,.resize={.ready=1},.profile_loaded=1};
  struct object top={.p=&top_proxy,.surface=&surface};
  struct csd_frame frame={.top=&top,.restore_width=800,.restore_height=600};
  top.csd=surface.csd=&frame;
  objects=&surface;task_count=1;tasks[0]=(struct task){.id=1,.package="com.test",.width=800,.height=600};
  /* Actual pointer callback: first header click moves, second toggles once. */
  frame.pieces[0].width=804;frame.pieces[0].height=38;
  struct object seat={.p=&seat_proxy};
  struct object pointer={.parent=&seat,.csd_focus=&frame,.csd_suppress=1,
                          .csd_part=0,.csd_x=100,.csd_y=20};
  csd_pointer_button(&pointer,NULL,1,1000,0x110,1);
  csd_pointer_button(&pointer,NULL,1,1010,0x110,0);
  csd_pointer_button(&pointer,NULL,2,1200,0x110,1);
  csd_pointer_button(&pointer,NULL,2,1210,0x110,0);
  assert(maximize_requests==1&&move_requests==1&&frame.maximized);
  assert(surface.profile_state_dirty);
  profile_state_poll();
  int dir=open(profile_root,PROFILE_DIRECTORY);assert(dir>=0);
  int w,h,maximized;
  assert(profile_read_state(dir,"com.test","r-test",&w,&h,&maximized)==1);
  assert(maximized&&w==800&&h==600);
  /* A genuinely new task restores maximize, retaining its normal bounds. */
  objects=NULL;for(int i=0;i<3;++i)profile_poll();tasks[0].id=2;
  struct object reopened={.app=1,.app_id="com.test",.has_bounds=1,
    .content_width=500,.content_height=400,.resize={.ready=1}};
  struct object reopened_top={.p=&top_proxy,.surface=&reopened};
  struct csd_frame reopened_frame={.top=&reopened_top};reopened.csd=&reopened_frame;
  objects=&reopened;settle();
  assert(maximize_requests==2&&reopened_frame.maximize_pending);
  assert(reopened_frame.restore_width==800&&reopened_frame.restore_height==600);
  assert(!reopened.resize.width); /* Never relayout to the saved normal size first. */
  /* Double-click again restores, and saves maximized=false. */
  objects=&surface;tasks[0].id=1;
  surface.content_width=1920;surface.content_height=996;
  csd_pointer_button(&pointer,NULL,3,2000,0x110,1);
  csd_pointer_button(&pointer,NULL,3,2010,0x110,0);
  csd_pointer_button(&pointer,NULL,4,2200,0x110,1);
  csd_pointer_button(&pointer,NULL,4,2210,0x110,0);
  assert(restore_requests==1&&!frame.maximized);
  profile_state_poll();
  assert(profile_read_state(dir,"com.test","r-test",&w,&h,&maximized)==1);
  assert(!maximized&&w==800&&h==600);
  /* Slow clicks / separate positions / separate windows are not double clicks. */
  struct csd_frame other={0};
  assert(!csd_header_double_click(&other,3000,10,10));
  assert(!csd_header_double_click(&other,3500,10,10));
  assert(!csd_header_double_click(&other,3600,30,10));
  assert(csd_header_double_click(&other,3700,30,10));
  assert(!csd_header_double_click(&other,3800,30,10));
  assert(!unlinkat(dir,"com.test",0));close(dir);assert(!rmdir(profile_root));
  while(profile_seen){struct profile_task_seen *next=profile_seen->next;free(profile_seen);profile_seen=next;}
  return 0;
}
