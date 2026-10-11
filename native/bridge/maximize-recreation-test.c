#define ANVIL_CSD_EXPERIMENT 1
#include "bridge.c"
#include <assert.h>

int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static struct proxy proxies[32], compositor, subcompositor, shm, top_proxy;
static int allocated, maximize_requests, unmaximize_requests;
static uint32_t version(struct proxy *p) {(void)p;return 1;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *args,
                            const struct interface *interface,uint32_t v) {
  (void)args;(void)interface;(void)v;
  if (p==&top_proxy) {
    if (op==9) ++maximize_requests;
    if (op==10) ++unmaximize_requests;
    return NULL;
  }
  assert(allocated<32);
  return &proxies[allocated++];
}
int main(void) {
  real_marshal=record;get_version=version;
  csd_compositor=&compositor;csd_subcompositor=&subcompositor;csd_shm=&shm;
  struct csd_frame old_frame={.maximized=1,.restore_width=1152,.restore_height=703,
                              .max_width=1916,.max_height=996};
  struct object old={.app=1,.app_id="com.android.vending",.csd=&old_frame};
  objects=&old;profile_tick=10;profile_observe_surfaces();
  task_count=1;tasks[0].id=84;
  snprintf(tasks[0].package,sizeof(tasks[0].package),"%s",old.app_id);
  profile_note_resize(old.app_id,84);
  /* Recreate before the next profile poll, as seen in the HWC log. */
  struct object fresh={.p=&proxies[31],.app=1,.app_id=old.app_id,.content_width=1916,.content_height=996};
  struct object top={.p=&top_proxy,.surface=&fresh};
  struct object xdg={.p=&proxies[30],.surface=&fresh};fresh.xdg=&xdg;
  objects=&fresh;
  csd_create(&top);
  assert(maximize_requests==1&&unmaximize_requests==0);
  assert(fresh.csd->maximized&&fresh.resize.maximized);
  assert(fresh.csd->restore_width==1152&&fresh.csd->restore_height==703);
  uint32_t state=1;
  struct {size_t size,alloc;uint32_t *data;} states={4,4,&state};
  state=0;
  configure(&top,&top_proxy,1082,750,&states);
  assert(fresh.csd->maximized&&fresh.csd->maximize_pending);
  assert(!fresh.resize.width&&fresh.csd->restore_width==1152);
  configure(&top,&top_proxy,1916,958,&states);
  assert(fresh.csd->maximized&&fresh.csd->restore_height==703);
  state=1;
  configure(&top,&top_proxy,1916+2*CSD_BORDER,996+CSD_HEADER+CSD_BORDER,&states);
  assert(fresh.csd->maximize_pending);
  state=0;
  configure(&top,&top_proxy,1024,682,&states);
  assert(fresh.csd->maximized&&!fresh.resize.width);
  assert(fresh.csd->restore_width==1152&&fresh.csd->restore_height==703);
  state=0;
  fresh.csd->maximize_pending=0; /* Recovery expires, or user clicks restore. */
  configure(&top,&top_proxy,0,0,&states);
  assert(!fresh.csd->maximized);
  assert(fresh.resize.width==1152&&fresh.resize.height==703);
  /* A normal resize must remain normal after recreation. */
  profile_note_resize(fresh.app_id,84);
  struct csd_frame normal={0};
  assert(profile_restore_frame(fresh.app_id,&normal)&&!normal.maximized);
  tasks[0].id=85;
  assert(!profile_restore_frame(fresh.app_id,&normal)); /* Genuine relaunch. */
  tasks[0].id=84;profile_tick+=200;
  assert(!profile_restore_frame(fresh.app_id,&normal)); /* Expired history. */
  free(top.csd);
  while(profile_seen) {
    struct profile_task_seen *next=profile_seen->next;
    free(profile_seen);profile_seen=next;
  }
  return 0;
}
