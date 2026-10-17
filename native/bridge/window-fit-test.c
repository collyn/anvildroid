/* Real configure/geometry/input callbacks: a requested host frame must survive
 * the old snapshot, the aligned Android result and repeated configure echoes. */
#include "bridge.c"
#include <assert.h>

int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static const struct interface si={.name="wl_surface"},vi={.name="wp_viewport"},
  xi={.name="xdg_surface"},subi={.name="wl_subsurface"},
  pool_i={.name="wl_shm_pool"},buffer_i={.name="wl_buffer"};
static struct proxy parent={&si},child={&si},pv={&vi},cv={&vi},xdg={&xi},sub={&subi},
  fake_pool={&pool_i},fake_shm={&si},buffers[128];
static int child_w,child_h,child_x,child_y,window_w,window_h;
static int requests,source_requests,content_attaches,painted_buffers;
static const char *backend_socket="";
int __system_property_get(const char *name,char *value) {
  if(strcmp(name,"mesa.vtest.socket.name"))return 0;
  return snprintf(value,92,"%s",backend_socket);
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static int listen_buffer(struct proxy *p,void (**listener)(void),void *data) {
  (void)p;(void)listener;(void)data;return 0;
}
static void destroy_proxy(struct proxy *p){(void)p;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)v;++requests;
  if(i==&pool_i) {
    uint32_t pixel=0;
    assert(read(a[1].i,&pixel,sizeof(pixel))==sizeof(pixel));
    if(a[2].i==4)assert(pixel==0xff000000); /* 1x1 letterbox remains opaque black. */
    else assert(pixel==0||pixel==0xff212121); /* Header edge repaints on state changes. */
    return &fake_pool;
  }
  if(i==&buffer_i) {
    assert(painted_buffers<128&&((a[2].i==1&&a[3].i==1)||
      (a[2].i>=64&&a[3].i==CSD_HEADER)));
    buffers[painted_buffers].interface=&buffer_i;
    return &buffers[painted_buffers++];
  }
  if((p==&parent||p==&child)&&op==1)++content_attaches;
  if(p==&cv&&op==1)++source_requests;
  if(p==&cv&&op==2){child_w=a[0].i;child_h=a[1].i;}
  if(p==&sub&&op==1){child_x=a[0].i;child_y=a[1].i;}
  if(p==&xdg&&op==3){window_w=a[2].i;window_h=a[3].i;}
  return 0;
}
int main(void) {
  /* wl_display/private wrappers can have no wl_interface at proxy offset 0.
   * Searching for viewporter must use captured public protocol metadata. */
  struct proxy opaque={0};
  struct object opaque_object={.p=&opaque,.protocol_name="wl_display"};
  struct object unready={.p=&parent};
  objects=&opaque_object;
  assert(!game_fit_create(&unready));
  objects=0;
  assert(!resize_uses_nvidia()); /* A host with no NVIDIA guest property. */
  backend_socket="/dev/other/venus.sock";resize_nvidia_backend=-1;
  assert(!resize_uses_nvidia());
  backend_socket="/dev/venus/venus.sock";resize_nvidia_backend=-1;
  assert(resize_uses_nvidia());
  struct game_fit_state f={0};
  struct proxy bars[4]={{&si},{&si},{&si},{&si}},bv[4]={{&vi},{&vi},{&vi},{&vi}},
    bs[4]={{&subi},{&subi},{&subi},{&subi}};
  for(int n=0;n<4;++n) {
    f.bars[n]=(struct csd_piece){.surface=&bars[n],.subsurface=&bs[n]};
    f.viewports[n]=&bv[n];
  }
  struct object region={.valid=1,.x=0,.y=32,.width=1024,.height=960};
  struct object root={.p=&parent,.app=1,.app_id="com.android.settings",.region=&region,
    .fit=&f,.profile_loaded=1,.resize={.ready=1}};
  struct object xs={.p=&xdg,.surface=&root};root.xdg=&xs;
  struct object parent_v={.p=&pv,.surface=&root,.width=1984,.height=2164};root.viewport=&parent_v;
  struct object layer={.p=&child};
  struct object child_v={.p=&cv,.surface=&layer,.width=1024,.height=960};layer.viewport=&child_v;
  struct object child_sub={.p=&sub,.surface=&layer,.parent=&root,.x=0,.y=32};
  root.next=&child_sub;child_sub.next=&layer;layer.next=&child_v;
  child_v.next=&parent_v;parent_v.next=&xs;objects=&root;
  for(struct object *o=objects;o;o=o->next)o->protocol_name=o->p->interface->name;
  scale_has_viewporter=1;decoration_available=1;real_marshal=record;get_version=version;
  real_listen=listen_buffer;real_destroy=destroy_proxy;
  csd_shm=&fake_shm;csd_pool_i=&pool_i;csd_buffer_i=&buffer_i;
  task_count=1;tasks[0]=(struct task){.id=1,.package="com.android.settings",.x=0,.y=32};
  struct object top={.surface=&root};
  uint32_t state=3;struct {size_t size,alloc;uint32_t *data;} states={4,4,&state};
  /* Music opens at 464x824. The old snapshot must not publish a second
   * 448x824 pitch correction over the intended compact 448x704 layout. */
  resize_nvidia_backend=1;hwc_scale_120=120;
  tasks[0].width=464;tasks[0].height=824;
  child_v.width=464;child_v.height=824;
  geometry(&root);
  queue_open_size(&root);
  assert(root.fit_w==448&&root.fit_h==704&&snap_resize_protected(&root));
  root.resize.cooldown=0;
  root.resize.dragging=1;
  assert(!resize_due(&root.resize));
  root.resize.dragging=0;
  assert(resize_due(&root.resize)); /* Initial layout has no 300ms debounce. */
  state=0;configure(&top,0,448,704,&states);geometry(&root);
  for(int tick=0;tick<RESIZE_QUIET_TICKS+2;++tick)resize_due(&root.resize);
  assert(root.resize.width==448&&root.resize.height==704);
  root.fit_delivered_w=root.resize.width;root.fit_delivered_h=root.resize.height;
  resize_submitted(&root.resize);
  for(int tick=0;tick<RESIZE_COOLDOWN_TICKS+10;++tick)resize_due(&root.resize);
  assert(!root.resize.cooldown&&snap_resize_protected(&root));
  tasks[0].width=448;tasks[0].height=704;
  child_v.width=448;child_v.height=704;geometry(&root);
  struct game_fit_rect opened=game_fit_area(&root);
  assert(opened.x==0&&opened.y==0&&opened.width==448&&opened.height==704);
  assert(!snap_resize_protected(&root));
  const int scales[]={120,150,180,240};
  for(int gpu=0;gpu<2;++gpu)for(unsigned n=0;n<sizeof(scales)/sizeof(scales[0]);++n) {
    resize_nvidia_backend=gpu;hwc_scale_120=scales[n];
    root.fit_w=root.fit_h=root.fit_delivered_w=root.fit_delivered_h=0;
    root.resize=(struct resize_policy){.ready=1};root.profile_dirty=0;
    tasks[0].width=1024;tasks[0].height=960;
    child_v.width=task_to_logical(tasks[0].width,1);
    child_v.height=task_to_logical(tasks[0].height,1);
    child_sub.y=task_to_logical(32,0);
    geometry(&root);
    int accepted=gpu?task_to_logical(resize_task_width(logical_to_task(465),1),1):465;
    state=3;configure(&top,0,465,701,&states);geometry(&root);
    assert(window_w==accepted&&window_h==701&&!root.preview_width);
    assert(root.resize.width==accepted&&root.profile_dirty);
    assert(!content_clip_enabled(&root));
    for(int tick=0;tick<30;++tick)assert(!resize_due(&root.resize));
    struct game_fit_rect area=game_fit_area(&root);
    assert(child_w==area.width&&child_h==area.height);
    assert(child_x==area.x&&child_y==area.y);
    assert(child_x>=0&&child_y>=0&&child_x+child_w<=accepted&&child_y+child_h<=701);
    state=0;configure(&top,0,465,701,&states);
    for(int tick=0;tick<RESIZE_QUIET_TICKS;++tick)assert(!resize_due(&root.resize));
    assert(resize_due(&root.resize));
    root.fit_delivered_w=root.resize.width;root.fit_delivered_h=root.resize.height;
    resize_submitted(&root.resize);geometry(&root);
    assert(window_w==accepted&&window_h==701&&!window_fit_settled(&root));
    tasks[0].width=resize_task_width(logical_to_task(accepted),gpu);
    tasks[0].height=logical_to_task(701);
    if(gpu)assert(tasks[0].width%64==0&&tasks[0].width<=logical_to_task(accepted));
    else assert(tasks[0].width==logical_to_task(accepted));
    child_v.width=task_to_logical(tasks[0].width,1);
    child_v.height=task_to_logical(tasks[0].height,1);
    geometry(&root);area=game_fit_area(&root);
    assert(window_w==accepted&&window_h==701&&area.y==0&&area.height==701);
    assert(window_fit_settled(&root));
    int left=area.x,right=accepted-area.x-area.width;
    assert(left>=0&&right>=0&&right-left<=1);
    assert(left==0&&right==0&&area.width==accepted);
    int settled=requests;
    for(int tick=0;tick<300;++tick)geometry(&root);
    assert(requests==settled); /* No repeated bars, content damage or commits. */
    configure(&top,0,465,701,&states);
    assert(!root.resize.width&&root.fit_w==accepted);
    /* The center maps to the same source point on parent and child. */
    int32_t input=(area.width/2)*256;
    int32_t expected=(int32_t)((int64_t)input*root.fit_sw/area.width);
    assert(game_fit_input(&child,input,0)==expected);
    assert(game_fit_input(&parent,input+area.x*256,0)==expected);
    /* Maximize aligns the Android task without changing the workarea frame. */
    state=1;configure(&top,0,1910,1000,&states);geometry(&root);
    assert(window_w==1910&&window_h==1000&&root.resize.maximized);
    root.fit_delivered_w=1910;root.fit_delivered_h=1000;
    resize_submitted(&root.resize);
    tasks[0].width=resize_task_width(logical_to_task(1910),gpu);
    tasks[0].height=logical_to_task(1000);
    child_v.width=task_to_logical(tasks[0].width,1);
    child_v.height=task_to_logical(tasks[0].height,1);
    geometry(&root);
    assert(window_w==1910&&window_h==1000&&window_fit_settled(&root));
    area=game_fit_area(&root);
    assert(area.x==0&&area.y==0&&area.width==1910&&area.height==1000);
    assert(child_x==0&&child_y==0&&child_w==1910&&child_h==1000);
    /* The same independent axes must invert pointer coordinates after the
     * GPU pitch adjustment, including at the right/bottom content edges. */
    assert(game_fit_input(&child,1910*256,0)==root.fit_sw*256);
    assert(game_fit_input(&child,1000*256,1)==root.fit_sh*256);
    if(gpu) {
      /* A 1px maximize change stays in the same pitch bucket: no Android
       * frame will arrive, so changing only the delivered target must still
       * repaint the fit and remove its temporary bars. */
      configure(&top,0,1911,1000,&states);geometry(&root);
      assert(!window_fit_settled(&root));
      root.fit_delivered_w=1911;root.fit_delivered_h=1000;
      root.resize.width=root.resize.height=0;
      geometry(&root);
      assert(window_fit_settled(&root)&&child_x==0&&child_y==0&&child_w==1911&&child_h==1000);
    }
  }
  /* A decoration manager can negotiate client chrome. Its presence must not
   * remove the header/border from xdg geometry on KWin. */
  struct proxy header={&si},header_sub={&subi};
  struct csd_frame frame={.top=&top,.restore_width=465,.restore_height=701};
  frame.pieces[0]=(struct csd_piece){.surface=&header,.subsurface=&header_sub,
    .width=465+2*CSD_BORDER,.height=CSD_HEADER};
  root.csd=&frame;hwc_scale_120=120;
  root.fit_w=root.fit_delivered_w=465;root.fit_h=root.fit_delivered_h=701;
  root.resize=(struct resize_policy){.ready=1};
  tasks[0].width=448;tasks[0].height=701;
  child_v.width=448;child_v.height=701;child_sub.y=32;
  geometry(&root);
  assert(window_w==465+2*CSD_BORDER&&window_h==701+CSD_HEADER+CSD_BORDER);
  state=1;configure(&top,0,1910+2*CSD_BORDER,1000+CSD_HEADER+CSD_BORDER,&states);
  assert(frame.restore_width==465&&frame.restore_height==701);
  assert(root.fit_w==1910&&root.fit_h==1000&&frame.maximized);
  frame.pieces[0].width=1910+2*CSD_BORDER;geometry(&root);
  assert(window_w==1910+2*CSD_BORDER&&window_h==1000+CSD_HEADER+CSD_BORDER);
  state=0;configure(&top,0,0,0,&states);
  assert(root.fit_w==448&&root.fit_h==701&&!frame.maximized);
  frame.pieces[0].width=448+2*CSD_BORDER;geometry(&root);
  assert(window_w==448+2*CSD_BORDER&&window_h==701+CSD_HEADER+CSD_BORDER);
  assert(window_layout_width(&root,160)>=160); /* Header minimum cannot snap below its constraints. */
  /* Keep resolution accepts the exact host size with no task relayout. */
  resize_mode_set(root.app_id,1);
  state=3;configure(&top,0,465+2*CSD_BORDER,701+CSD_HEADER+CSD_BORDER,&states);
  assert(root.fit_w==465&&!root.resize.width&&root.resize.dragging);
  state=0;configure(&top,0,465+2*CSD_BORDER,701+CSD_HEADER+CSD_BORDER,&states);
  assert(root.fit_w==465&&!root.resize.width&&!root.resize.dragging);
  struct game_fit_rect fixed=game_fit_area(&root);
  assert(fixed.x>0&&fixed.width<root.fit_w); /* Keep resolution still contains. */
  assert(!source_requests&&!content_attaches&&painted_buffers>0);
  /* Size-menu changes use the same presentation and input transform as a
   * compositor resize, while the old Android snapshot is still visible. */
  backend_socket="";resize_nvidia_backend=-1;
  resize_mode_set(root.app_id,0);
  resize_mode_set(root.app_id,1);
  frame.menu_open=1;frame.menu_submenu=2;
  csd_menu_activate(&frame,CSD_MENU_CUSTOM);
  assert(root.custom_size.enabled&&!resize_mode_fixed(root.app_id));
  csd_menu_size(&frame,480,800);
  assert(!root.custom_size.enabled);
  assert(root.fit_w==480&&root.fit_h==800&&root.resize.width==480);
  assert(window_w==480+2*CSD_BORDER&&window_h==800+CSD_HEADER+CSD_BORDER);
  frame.menu_open=1;frame.menu_submenu=2;
  csd_menu_activate(&frame,CSD_MENU_LANDSCAPE);
  assert(root.fit_w==800&&root.fit_h==480&&root.resize.width==800&&root.resize.height==480);
  assert(window_w==800+2*CSD_BORDER&&window_h==480+CSD_HEADER+CSD_BORDER);
  struct game_fit_rect area=game_fit_area(&root);
  int32_t center=area.width*128;
  assert(game_fit_input(&child,center,0)==(int32_t)((int64_t)center*root.fit_sw/area.width));
  assert(game_fit_input(&parent,center+area.x*256,0)==game_fit_input(&child,center,0));
  assert(!source_requests&&!content_attaches); /* Real Android buffers stay untouched. */
  while(csd_buffers)csd_release(csd_buffers,csd_buffers->proxy);
  objects=0;
  return 0;
}
