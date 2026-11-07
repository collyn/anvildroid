/* A bridge-chosen host size (profile restore, open size, Size menu) must not
 * be undone by a configure the compositor queued for the previous size.
 * Live X11 (Muffin) case: Gallery mapped at Android's default 464x824 task,
 * its profile restored 448x704, and the activation configure for 466x858 was
 * still in flight. The X11 adapter configures every observed resize, so the
 * stale echo and the restored size chased each other: 466x858 <-> 450x738. */
static char profile_root[128];
#define PROFILE_ROOT profile_root
#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
extern int setenv(const char *, const char *, int);
extern int rmdir(const char *);
extern void *stderr;

int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static const struct interface si={.name="wl_surface"},vi={.name="wp_viewport"},
  xi={.name="xdg_surface"},subi={.name="wl_subsurface"},
  pool_i={.name="wl_shm_pool"},buffer_i={.name="wl_buffer"};
static struct proxy parent={&si},child={&si},pv={&vi},cv={&vi},xdg={&xi},sub={&subi},
  fake_pool={&pool_i},fake_shm={&si},buffers[512];
static int window_w,window_h,painted_buffers;
int __system_property_get(const char *name,char *value) {(void)name;value[0]=0;return 0;}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static int listen_buffer(struct proxy *p,void (**listener)(void),void *data) {
  (void)p;(void)listener;(void)data;return 0;
}
static void destroy_proxy(struct proxy *p){(void)p;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)v;
  if(i==&pool_i)return &fake_pool;
  if(i==&buffer_i) {
    buffers[painted_buffers%512].interface=&buffer_i;
    return &buffers[painted_buffers++%512];
  }
  if(p==&xdg&&op==3){window_w=a[2].i;window_h=a[3].i;}
  return 0;
}

/* Fixture: one fitted Gallery window with CSD, as the HWC creates it. */
static struct game_fit_state f;
static struct proxy bars[4]={{&si},{&si},{&si},{&si}},bv[4]={{&vi},{&vi},{&vi},{&vi}},
  bs[4]={{&subi},{&subi},{&subi},{&subi}},header={&si},header_sub={&subi};
static struct object region,root,xs,parent_v,layer,child_v,child_sub,top;
static struct csd_frame frame;
static uint32_t activated=4;
static struct {size_t size,alloc;uint32_t *data;} states={4,4,&activated};
static const char *package="com.android.gallery3d";

static void open_window(int task_w,int task_h) {
  memset(&f,0,sizeof(f));
  for(int n=0;n<4;++n) {
    f.bars[n]=(struct csd_piece){.surface=&bars[n],.subsurface=&bs[n]};
    f.viewports[n]=&bv[n];
  }
  region=(struct object){.valid=1,.x=0,.y=0,.width=task_w,.height=task_h};
  root=(struct object){.p=&parent,.app=1,.app_id=(char *)package,.region=&region,
    .fit=&f,.resize={.ready=1},.initial_presented=1};
  xs=(struct object){.p=&xdg,.surface=&root};root.xdg=&xs;
  parent_v=(struct object){.p=&pv,.surface=&root,.width=1984,.height=1280};
  root.viewport=&parent_v;
  layer=(struct object){.p=&child};
  child_v=(struct object){.p=&cv,.surface=&layer,.width=task_w,.height=task_h};
  layer.viewport=&child_v;
  child_sub=(struct object){.p=&sub,.surface=&layer,.parent=&root,.x=0,.y=32};
  root.next=&child_sub;child_sub.next=&layer;layer.next=&child_v;
  child_v.next=&parent_v;parent_v.next=&xs;xs.next=0;objects=&root;
  for(struct object *o=objects;o;o=o->next)o->protocol_name=o->p->interface->name;
  top=(struct object){.surface=&root};
  frame=(struct csd_frame){.top=&top};
  frame.pieces[0]=(struct csd_piece){.surface=&header,.subsurface=&header_sub,
    .width=task_w+2*CSD_BORDER,.height=CSD_HEADER};
  root.csd=&frame;
  task_count=1;
  tasks[0]=(struct task){.id=31,.x=0,.y=32,.width=task_w,.height=task_h};
  snprintf(tasks[0].package,sizeof(tasks[0].package),"%s",package);
  geometry(&root); /* Mapped at Android's default freeform bounds. */
  assert(window_w==task_w+2*CSD_BORDER&&window_h==task_h+CSD_HEADER+CSD_BORDER);
}

/* Adapter model of native/x11/wayland-host.cpp: a commit requests the
 * accepted frame only if it differs from the last *observed* X11 size
 * (platform.resize); ConfigureNotify sends xdg configure for every observed
 * change. Each hop takes one tick; the WM applies a request one tick later
 * and its ConfigureNotify is handled before commits of the same tick. Sizes
 * are xdg geometry (the 4/4/0/4 input margins cancel out). */
struct size {int w,h;};
struct queue {struct size item[8];int count;};
static void push(struct queue *q,int w,int h) {
  assert(q->count<8);q->item[q->count++]=(struct size){w,h};
}
static int run_adapter(struct size observed,struct queue commits,
                       struct queue configures,int ticks) {
  struct queue wm={0};
  int changes=0;
  for(int tick=0;tick<ticks;++tick) {
    struct queue wm_now=wm,commit_now=commits,configure_now=configures;
    wm=commits=configures=(struct queue){0};
    for(int i=0;i<wm_now.count;++i) {
      struct size r=wm_now.item[i];
      if(r.w==observed.w&&r.h==observed.h)continue;
      observed=r;++changes;
      push(&configures,observed.w,observed.h);
    }
    for(int i=0;i<commit_now.count;++i) {
      struct size g=commit_now.item[i];
      if(g.w!=observed.w||g.h!=observed.h)push(&wm,g.w,g.h);
    }
    for(int i=0;i<configure_now.count;++i) {
      /* The bridge's configure listener, then its ack_configure geometry()
       * and state-only commit (bridge.c ack_configure path). */
      configure(&top,0,configure_now.item[i].w,configure_now.item[i].h,&states);
      geometry(&root);
      push(&commits,window_w,window_h);
    }
  }
  return changes;
}

int main(void) {
  snprintf(profile_root,sizeof(profile_root),"/tmp/anvil-configure-echo-XXXXXX");
  assert(mkdtemp(profile_root));
  assert(!setenv("ANVILDROID_RUNTIME_ID","r-unit",1));
  scale_has_viewporter=1;decoration_available=1;real_marshal=record;get_version=version;
  real_listen=listen_buffer;real_destroy=destroy_proxy;
  csd_shm=&fake_shm;csd_pool_i=&pool_i;csd_buffer_i=&buffer_i;
  hwc_scale_120=120;resize_nvidia_backend=0; /* Intel: no pitch alignment. */
  int dir=open(profile_root,PROFILE_DIRECTORY);assert(dir>=0);
  assert(profile_write(dir,package,"r-unit",448,704));

  /* 1. Profile restore while the 466x858 activation configure is queued. */
  open_window(464,824);
  for(int i=0;i<4&&!root.profile_loaded;++i)profile_poll();
  assert(root.profile_loaded&&root.profile_restoring);
  assert(root.fit_w==448&&root.fit_h==704&&root.resize.width==448);
  geometry(&root); /* Next HWC commit publishes the restored frame. */
  assert(window_w==450&&window_h==738);
  struct queue commits={0},configures={0};
  push(&commits,450,738);  /* Bridge: restored frame. */
  push(&configures,466,858); /* Adapter: FocusIn at the mapped size. */
  int changes=run_adapter((struct size){466,858},commits,configures,200);
  fprintf(stderr,"profile restore: X11 size changes=%d final fit=%dx%d\n",
          changes,root.fit_w,root.fit_h);
  assert(changes==1); /* 466x858 -> 450x738 once; never back. */
  assert(root.fit_w==448&&root.fit_h==704&&window_w==450&&window_h==738);
  /* The restored Android relayout survives; the stale echo did not cancel it
   * or turn it into a 464x824 request. */
  assert(root.resize.width==448&&root.resize.height==704);
  assert(frame.restore_width==448&&frame.restore_height==704);

  /* 2. After the compositor confirmed 450x738, the old size is a genuine
   * user resize again (e.g. a WM drag back to it) and is accepted. */
  configure(&top,0,466,858,&states);geometry(&root);
  assert(root.fit_w==464&&root.fit_h==824&&window_w==466&&window_h==858);

  /* 3. A different size while the echo is pending is the user's and wins. */
  open_window(464,824);
  root.profile_loaded=1;
  queue_open_size(&root);geometry(&root);
  assert(window_w==450&&window_h==738);
  configure(&top,0,602,502,&states);geometry(&root);
  assert(root.fit_w==600&&root.fit_h==468&&window_w==602&&window_h==502);

  /* 4. Open size and the Size menu supersede the published frame too. */
  open_window(464,824);
  root.profile_loaded=1;
  queue_open_size(&root);geometry(&root);
  commits=configures=(struct queue){0};
  push(&commits,450,738);push(&configures,466,858);
  changes=run_adapter((struct size){466,858},commits,configures,200);
  assert(changes==1&&root.fit_w==448&&root.fit_h==704);
  csd_menu_size(&frame,480,800);
  assert(window_w==482&&window_h==834);
  commits=configures=(struct queue){0};
  push(&commits,482,834);push(&configures,450,738);
  changes=run_adapter((struct size){450,738},commits,configures,200);
  assert(changes==1&&root.fit_w==480&&root.fit_h==800);

  /* 5. If the compositor never confirms the chosen size, its configure is
   * authoritative again after the bounded wait. */
  open_window(464,824);
  root.profile_loaded=1;
  queue_open_size(&root);geometry(&root);
  for(int tick=0;tick<CONFIGURE_ECHO_TICKS;++tick)host_echo_tick();
  configure(&top,0,466,858,&states);geometry(&root);
  assert(root.fit_w==464&&root.fit_h==824);

  while(csd_buffers)csd_release(csd_buffers,csd_buffers->proxy);
  objects=0;
  assert(!unlinkat(dir,package,0));close(dir);assert(!rmdir(profile_root));
  fprintf(stderr,"configure echo: PASS\n");
  return 0;
}
