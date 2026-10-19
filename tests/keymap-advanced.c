#define main mapping_baseline_main
#include "scrcpy-mapping.c"
#undef main
static int forwarded_pointer;
static void input_enter(void *d,struct proxy *p,uint32_t serial,struct proxy *surface,int32_t x,int32_t y){(void)d;(void)p;(void)serial;(void)surface;(void)x;(void)y;}
static void input_leave(void *d,struct proxy *p,uint32_t serial,struct proxy *surface){(void)d;(void)p;(void)serial;(void)surface;}
static void input_motion(void *d,struct proxy *p,uint32_t t,int32_t x,int32_t y){(void)d;(void)p;(void)t;(void)x;(void)y;++forwarded_pointer;}
static void input_button(void *d,struct proxy *p,uint32_t serial,uint32_t t,uint32_t b,uint32_t state){(void)d;(void)p;(void)serial;(void)t;(void)b;(void)state;++forwarded_pointer;}
static void input_axis(void *d,struct proxy *p,uint32_t t,uint32_t axis,int32_t value){(void)d;(void)p;(void)t;(void)axis;(void)value;++forwarded_pointer;}
static void input_frame(void *d,struct proxy *p){(void)d;(void)p;++forwarded_pointer;}
static void input_source(void *d,struct proxy *p,uint32_t source){(void)d;(void)p;(void)source;++forwarded_pointer;}
static void input_stop(void *d,struct proxy *p,uint32_t t,uint32_t axis){(void)d;(void)p;(void)t;(void)axis;++forwarded_pointer;}
static void input_discrete(void *d,struct proxy *p,uint32_t axis,int32_t steps){(void)d;(void)p;(void)axis;(void)steps;++forwarded_pointer;}
static const struct interface repaint_pool_i={.name="wl_shm_pool"},repaint_buffer_i={.name="wl_buffer"};
static struct proxy repaint_pool={&repaint_pool_i},repaint_buffer={&repaint_buffer_i};
static struct proxy *repaint_marshal(struct proxy *p,uint32_t op,union argument *a,const struct interface *i,uint32_t v){
  (void)p;(void)op;(void)a;(void)v;
  if(i==&repaint_pool_i)return &repaint_pool;
  if(i==&repaint_buffer_i)return &repaint_buffer;
  return 0;
}
static int repaint_listen(struct proxy *p,void(**listener)(void),void *data){(void)p;(void)listener;(void)data;return 0;}
static void repaint_destroy(struct proxy *p){(void)p;}
static uint32_t repaint_version(struct proxy *p){(void)p;return 1;}
static int repaint_flush(struct wl_display *display){(void)display;return 0;}
static void setup(struct object *s,struct keymap_overlay *h,int kind) {
  scrcpy_mapping_cancel();memset(map_held,0,sizeof(map_held));memset(map_mouse_latched,0,sizeof(map_mouse_latched));map_fd=-1;unavailable=0;sequence_count=0;
  *s=(struct object){.app=1,.has_bounds=1,.app_id="org.test",.bw=464,.bh=824};objects=s;
  tasks[0]=(struct task){.id=5,.x=760,.y=154,.width=464,.height=824,.package="org.test"};task_count=1;
  *h=(struct keymap_overlay){.passive=1,.task=5,.package="org.test",.model={.count=1,
    .points={{.key=17,.nx=32768,.ny=32768,.kind=kind,.ex=49152,.ey=32768,.duration=100,.repeats=3}}}};
  if(kind==KEYMAP_MOUSE_TOUCH){h->model.points[0].key=0;h->model.points[0].source=KEYMAP_SOURCE_POINTER;}
  if(kind==KEYMAP_LOOK){h->model.points[0].look_width=8192;h->model.points[0].look_height=8192;h->model.points[0].sensitivity_x=h->model.points[0].sensitivity_y=100;}
  assert(keymap_valid(&h->model,1));
}
int main(void) {
  struct object s;struct keymap_overlay h;
  setup(&s,&h,KEYMAP_DPAD);
  assert(scrcpy_mapping_key(&s,&h,17,1)&&map_count==1);
  assert(sequence_count==2&&sequence[0][1]==0&&sequence[1][1]==2);
  assert(scrcpy_mapping_key(&s,&h,32,1)&&map_count==1);
  assert(map_advanced[0].dx==1&&map_advanced[0].dy==-1&&last[1]==2);
  assert(map_advanced[0].lx==32768+11585&&map_advanced[0].ly==32768-11585);
  scrcpy_mapping_key(&s,&h,30,1);assert(map_advanced[0].dx==0);
  scrcpy_mapping_key(&s,&h,30,0);assert(map_advanced[0].dx==1);
  scrcpy_mapping_key(&s,&h,17,0);assert(map_count==1&&map_advanced[0].dy==0);
  scrcpy_mapping_key(&s,&h,32,0);assert(!map_count&&last[1]==1);
  setup(&s,&h,KEYMAP_SWIPE);
  scrcpy_mapping_key(&s,&h,17,1);int before=packets;
  map_advanced_advance(50);assert(packets==before+1&&last[1]==2&&map_count==1);
  map_advanced_advance(50);assert(packets==before+3&&!map_count&&last[1]==1);
  setup(&s,&h,KEYMAP_REPEAT);
  scrcpy_mapping_key(&s,&h,17,1);before=packets;
  for(int i=0;i<4;++i)map_advanced_advance(50);
  assert(packets==before+2&&last[1]==0&&map_count==1);
  scrcpy_mapping_key(&s,&h,17,0);assert(!map_count);
  setup(&s,&h,KEYMAP_MACRO);
  scrcpy_mapping_key(&s,&h,17,1);
  map_advanced_advance(100); /* release origin */
  map_advanced_advance(100); /* press endpoint */
  int endpoint_x=map_advanced[0].lx,endpoint_y=map_advanced[0].ly;
  assert(endpoint_x==h.model.points[0].ex);
  map_advanced_advance(100); /* release must preserve endpoint, never jump */
  assert(last[1]==1&&map_advanced[0].lx==endpoint_x&&map_advanced[0].ly==endpoint_y);
  for(int i=0;i<4;++i)map_advanced_advance(50);
  assert(!map_count&&!map_advanced[0].owner);
  h.model.points[0].duration=5000;assert(!keymap_valid(&h.model,1));
  setup(&s,&h,KEYMAP_SKILL);
  struct proxy root={0};s.p=&root;s.viewport=&s;scale_has_viewporter=1;
  struct object pointer={.pointer_surface=&root};
  scrcpy_mapping_key(&s,&h,17,1);
  assert(map_advanced_motion(&pointer,960*256,454*256)&&last[1]==2);
  assert(map_advanced[0].lx==keymap_normalize(200,464)&&map_advanced[0].ly==keymap_normalize(300,824));
  scrcpy_mapping_key(&s,&h,17,0);assert(!map_count&&last[1]==1);
  setup(&s,&h,KEYMAP_AIM);s.p=&root;s.viewport=&s;absolute_pointer_enabled=0;
  scrcpy_mapping_key(&s,&h,17,1);
  assert(absolute_pointer_button(&pointer,0x110,1)&&map_count==2);
  assert(absolute_pointer_update(&pointer,860*256,354*256,1));
  assert(absolute_pointer_button(&pointer,0x110,0)&&map_count==1);
  map_advanced_button(&pointer,0x110,1);scrcpy_mapping_key(&s,&h,17,0);assert(!map_count);
  assert(absolute_pointer_button(&pointer,0x110,0)); /* no orphan release to app */
  assert(!absolute_pointer_update(&pointer,860*256,354*256,1)); /* forwarding restored */
  assert(!absolute_pointer_button(&pointer,0x110,1));
  /* Reuse a lower free slot for fire while aim occupies a higher slot. */
  setup(&s,&h,KEYMAP_AIM);s.p=&root;s.viewport=&s;
  h.model.count=2;h.model.points[1]=(struct keymap_point){.key=100,.nx=100,.ny=100};
  scrcpy_mapping_key(&s,&h,100,1); /* basic contact owns slot zero */
  scrcpy_mapping_key(&s,&h,17,1);assert(map_advanced[1].point.kind==KEYMAP_AIM);
  scrcpy_mapping_key(&s,&h,100,0);assert(!map_points[0]);
  assert(absolute_pointer_button(&pointer,0x110,1)&&map_fire_slot==0);
  int fire_x=map_advanced[0].lx,fire_y=map_advanced[0].ly;
  assert(absolute_pointer_update(&pointer,860*256,354*256,1));
  assert(map_advanced[0].lx==fire_x&&map_advanced[0].ly==fire_y);
  assert(map_advanced[1].lx==keymap_normalize(100,464)&&last[9]==1);
  assert(absolute_pointer_button(&pointer,0x110,0));scrcpy_mapping_key(&s,&h,17,0);
  assert(!map_count);
  /* Cancellation owns timers and all contacts; subsequent ticks do nothing. */
  setup(&s,&h,KEYMAP_REPEAT);scrcpy_mapping_key(&s,&h,17,1);
  scrcpy_mapping_cancel();before=packets;
  for(int i=0;i<100;++i)map_advanced_advance(50);
  assert(packets==before&&!map_count);
  /* A task replacement or changed task rectangle cancels before more motion. */
  setup(&s,&h,KEYMAP_REPEAT);scrcpy_mapping_key(&s,&h,17,1);
  tasks[0].width--;map_advanced_advance(50);assert(!map_count);
  setup(&s,&h,KEYMAP_REPEAT);scrcpy_mapping_key(&s,&h,17,1);
  tasks[0].id++;map_advanced_advance(50);assert(!map_count);
  setup(&s,&h,KEYMAP_REPEAT);unavailable=1;before=packets;
  scrcpy_mapping_key(&s,&h,17,1);assert(!map_count&&packets==before);
  /* A short sink write clears all pending state; ticks never reconnect/replay. */
  setup(&s,&h,KEYMAP_SWIPE);scrcpy_mapping_key(&s,&h,17,1);
  unavailable=2;map_advanced_advance(50);assert(!map_count&&map_fd==-1);
  unavailable=0;before=packets;map_advanced_advance(1000);assert(packets==before);
  /* A delayed macro poll finishes without replaying a burst of missed clicks. */
  setup(&s,&h,KEYMAP_MACRO);scrcpy_mapping_key(&s,&h,17,1);before=packets;
  map_advanced_advance(10000);assert(!map_count&&packets==before+1);
  /* Ten Android contacts maximum; an eleventh control cannot queue work. */
  setup(&s,&h,KEYMAP_REPEAT);h.model.count=11;
  for(int i=0;i<11;++i){h.model.points[i]=h.model.points[0];h.model.points[i].key=100+i;}
  for(int i=0;i<11;++i)scrcpy_mapping_key(&s,&h,100+i,1);
  assert(map_count==10);
  scrcpy_mapping_key(&s,&h,100,0);assert(map_count==9);
  before=packets;scrcpy_mapping_key(&s,&h,110,1);assert(map_count==9&&packets==before);
  scrcpy_mapping_key(&s,&h,110,0);scrcpy_mapping_key(&s,&h,110,1);assert(map_count==10);
  scrcpy_mapping_cancel();assert(!map_count);
  /* Swipe hold reaches its endpoint, stays there, and releases without reversal. */
  setup(&s,&h,KEYMAP_SWIPE);h.model.points[0].end_mode=KEYMAP_END_HOLD;
  scrcpy_mapping_key(&s,&h,17,1);map_advanced_advance(100);
  assert(map_count==1&&last[1]==2&&map_advanced[0].lx==49152);
  before=packets;map_advanced_advance(1000);assert(packets==before);
  scrcpy_mapping_key(&s,&h,17,0);assert(!map_count&&last[1]==1);
  /* A bounded tap cannot replay while the original physical key remains held. */
  setup(&s,&h,KEYMAP_BASIC);h.model.points[0].end_mode=KEYMAP_END_RELEASE;
  scrcpy_mapping_key(&s,&h,17,1);map_advanced_advance(100);assert(!map_count);
  before=packets;scrcpy_mapping_key(&s,&h,17,1);assert(packets==before);
  scrcpy_mapping_key(&s,&h,17,0);scrcpy_mapping_key(&s,&h,17,1);assert(map_count==1);
  /* Authored physical radius produces equal pixel displacement on each axis. */
  setup(&s,&h,KEYMAP_DPAD);h.model.points[0].radius=8192;h.model.points[0].ramp_ms=100;
  scrcpy_mapping_key(&s,&h,17,1);assert(sequence_count==1&&last[1]==0);
  map_advanced_advance(50);assert(map_advanced[0].ly<32768&&map_advanced[0].ly>32768-4612);
  map_advanced_advance(50);assert(map_advanced[0].ly==32768-(8192*464/824));
  /* Free mouse is a finger: no hover/wheel packets, release retains last position. */
  setup(&s,&h,KEYMAP_MOUSE_TOUCH);h.model.points[0].key=0;h.model.points[0].source=KEYMAP_SOURCE_POINTER;
  s.p=&root;s.viewport=&s;s.hints=&h;absolute_pointer_enabled=0;
  before=packets;assert(absolute_pointer_update(&pointer,860*256,354*256,1));assert(packets==before);
  assert(absolute_pointer_axis(&pointer,0,256)&&packets==before);
  assert(absolute_pointer_button(&pointer,272,1)&&map_count==1&&last[1]==0);
  assert(absolute_pointer_update(&pointer,870*256,364*256,1)&&last[1]==2);
  int finger_x=map_advanced[0].lx;
  assert(absolute_pointer_button(&pointer,272,0)&&!map_count&&last[1]==1);
  assert(finger_x==keymap_normalize(110,464));
  absolute_pointer_button(&pointer,272,1);scrcpy_mapping_cancel();before=packets;
  absolute_pointer_button(&pointer,272,1);assert(packets==before); /* held, cancelled */
  assert(absolute_pointer_button(&pointer,272,0));assert(packets==before);
  /* Relative hold look seeds its first event and lifts before recentering. */
  setup(&s,&h,KEYMAP_LOOK);s.p=&root;s.viewport=&s;s.hints=&h;
  h.model.points[0].look_width=1000;h.model.points[0].look_height=1000;
  scrcpy_mapping_key(&s,&h,17,1);before=packets;
  assert(absolute_pointer_update(&pointer,860*256,354*256,1)&&packets==before);
  assert(absolute_pointer_update(&pointer,861*256,354*256,1)&&last[1]==2);
  before=sequence_count;absolute_pointer_update(&pointer,900*256,354*256,1);
  assert(sequence_count==before+3&&sequence[before][1]==2&&sequence[before+1][1]==1&&sequence[before+2][1]==0);
  assert(scrcpy_mapping_escape()&&!map_count);
  /* Large motion stays bounded and still drags forward before recentering. */
  setup(&s,&h,KEYMAP_LOOK);h.model.points[0].sensitivity_x=1000;
  scrcpy_mapping_key(&s,&h,17,1);before=sequence_count;
  assert(map_look_delta(0,(int64_t)2147483647*65535/(256*464),0));
  assert(sequence_count==before+3&&sequence[before][1]==2&&sequence[before+1][1]==1&&sequence[before+2][1]==0);
  assert(map_advanced[0].lx==32768);
  /* Captured deltas use the same inverse viewport and HWC scale as hold
   * look. Half-size presentation at 2x HWC scale turns one host pixel into four. */
  setup(&s,&h,KEYMAP_LOOK);s.p=&root;s.viewport=&s;s.hints=&h;
  s.fit_sw=464;s.fit_sh=824;s.fit_w=232;s.fit_h=412;hwc_scale_120=240;
  struct object capture_keyboard={.keyboard_focus=&s};
  pointer=(struct object){.pointer_surface=&root};s.next=&capture_keyboard;capture_keyboard.next=&pointer;
  scrcpy_mapping_key(&s,&h,17,1);map_advanced[0].point.activation=1;
  struct proxy fake_lock={0};keymap_capture.owner=&s;keymap_capture.pointer=&pointer;
  keymap_capture.locked=&fake_lock;keymap_capture.active=1;
  scrcpy_mapping_relative(&s,256,0);assert(map_advanced[0].lx==32768+4*65535/464);
  for(int n=0;n<16;++n)scrcpy_mapping_relative(&s,1,0);
  assert(map_advanced[0].lx==32768+(int)((int64_t)272*4*65535/(256*464)));
  memset(&keymap_capture,0,sizeof(keymap_capture));hwc_scale_120=120;
  /* Mouse touch and hold look both follow pointer motion when authored together. */
  setup(&s,&h,KEYMAP_LOOK);s.p=&root;s.viewport=&s;s.hints=&h;
  h.model.count=2;h.model.points[1]=(struct keymap_point){.kind=KEYMAP_MOUSE_TOUCH,.source=KEYMAP_SOURCE_POINTER,.duration=100,.repeats=1};
  scrcpy_mapping_key(&s,&h,17,1);absolute_pointer_update(&pointer,860*256,354*256,1);
  absolute_pointer_button(&pointer,272,1);assert(map_count==2);
  before=packets;absolute_pointer_update(&pointer,861*256,355*256,1);assert(packets==before+2);
  absolute_pointer_button(&pointer,272,0);scrcpy_mapping_key(&s,&h,17,0);assert(!map_count);
  /* Joystick, look, and fixed fire own independent numeric fingers. */
  setup(&s,&h,KEYMAP_DPAD);s.p=&root;s.viewport=&s;s.hints=&h;
  h.model.count=4;
  h.model.points[1]=(struct keymap_point){.key=42,.kind=KEYMAP_LOOK,.nx=32768,.ny=32768,.duration=100,.repeats=1,.look_width=8192,.look_height=8192,.sensitivity_x=100,.sensitivity_y=100};
  h.model.points[2]=(struct keymap_point){.key=272,.kind=KEYMAP_BASIC,.source=KEYMAP_SOURCE_MOUSE,.nx=60000,.ny=60000,.end_mode=KEYMAP_END_HOLD};
  h.model.points[3]=(struct keymap_point){.kind=KEYMAP_MOUSE_TOUCH,.source=KEYMAP_SOURCE_POINTER,.nx=32768,.ny=32768,.duration=100,.repeats=1};
  scrcpy_mapping_key(&s,&h,17,1);scrcpy_mapping_key(&s,&h,42,1);
  absolute_pointer_update(&pointer,860*256,354*256,1);
  absolute_pointer_button(&pointer,272,1);assert(map_count==3);
  int fire_slot=-1;
  for(int i=0;i<10;++i)if(map_advanced[i].point.source==KEYMAP_SOURCE_MOUSE)fire_slot=i;
  assert(fire_slot>=0&&map_advanced[fire_slot].lx==60000);
  absolute_pointer_update(&pointer,861*256,354*256,1);
  assert(map_advanced[fire_slot].lx==60000&&map_count==3);
  absolute_pointer_button(&pointer,272,0);assert(map_count==2);
  scrcpy_mapping_key(&s,&h,17,0);assert(map_count==1);
  scrcpy_mapping_key(&s,&h,42,0);assert(!map_count);

  /* Exercise the real pointer listeners: active playback consumes raw motion,
   * buttons and wheel even when the optional absolute mouse path is disabled. */
  setup(&s,&h,KEYMAP_MOUSE_TOUCH);s.p=&root;s.viewport=&s;s.hints=&h;
  void (*callbacks[])(void)={(void(*)(void))input_enter,(void(*)(void))input_leave,(void(*)(void))input_motion,(void(*)(void))input_button,(void(*)(void))input_axis,(void(*)(void))input_frame,(void(*)(void))input_source,(void(*)(void))input_stop,(void(*)(void))input_discrete};
  pointer=(struct object){.listener=callbacks};absolute_pointer_enabled=0;
  csd_pointer_enter(&pointer,0,1,&root,860*256,354*256);
  before=packets;csd_pointer_motion(&pointer,0,2,861*256,355*256);
  csd_pointer_axis(&pointer,0,2,0,256);
  csd_pointer_frame(&pointer,0);csd_pointer_source(&pointer,0,0);
  csd_pointer_stop(&pointer,0,2,0);csd_pointer_discrete(&pointer,0,0,1);
  csd_pointer_button(&pointer,0,2,2,273,1);csd_pointer_button(&pointer,0,2,2,273,0);
  assert(packets==before&&!forwarded_pointer);
  csd_pointer_button(&pointer,0,3,3,272,1);assert(map_count==1&&!forwarded_pointer);
  csd_pointer_motion(&pointer,0,4,862*256,356*256);assert(last[1]==2&&!forwarded_pointer);
  csd_pointer_button(&pointer,0,5,5,272,0);assert(!map_count&&last[1]==1&&!forwarded_pointer);
  csd_pointer_button(&pointer,0,5,5,272,1);assert(map_count==1);
  scrcpy_mapping_cancel();pointer.csd_suppress=1;pointer.focus_blocked_button=272;before=packets;
  csd_pointer_button(&pointer,0,5,5,272,0);assert(packets==before&&!map_mouse_latched[0]&&!pointer.focus_blocked_button);
  pointer.csd_suppress=0;
  csd_pointer_button(&pointer,0,5,5,272,1);assert(map_count==1);
  csd_pointer_button(&pointer,0,5,5,272,0);assert(!map_count);
  s.keymap_disabled=1;
  csd_pointer_motion(&pointer,0,6,863*256,357*256);
  csd_pointer_axis(&pointer,0,6,0,256);
  csd_pointer_button(&pointer,0,7,7,272,1);assert(forwarded_pointer==3);
  /* A root-to-child transfer in one dispatch preserves keyboard contacts;
   * a genuine unmatched pointer leave cancels at the dispatch boundary. */
  setup(&s,&h,KEYMAP_DPAD);s.p=&root;s.viewport=&s;s.hints=&h;
  struct proxy child_proxy={0},sub_proxy={0};
  struct object layer={.p=&child_proxy},sub={.p=&sub_proxy,.protocol_name="wl_subsurface",.surface=&layer,.parent=&s};
  pointer=(struct object){.listener=callbacks};
  s.next=&sub;sub.next=&layer;layer.next=&pointer;
  csd_pointer_enter(&pointer,0,10,&root,860*256,354*256);
  scrcpy_mapping_key(&s,&h,17,1);before=packets;
  assert(game_fit_owner(find(&root))==&s);assert(game_fit_owner(find(&child_proxy))==&s);
  csd_pointer_leave(&pointer,0,11,&root);assert(map_count==1&&pointer.keymap_pointer_leave_pending);
  csd_pointer_enter(&pointer,0,12,&child_proxy,860*256,354*256);
  assert(!pointer.keymap_pointer_leave_pending);keymap_finish_pointer_leaves(0);assert(map_count==1);assert(packets==before);
  csd_pointer_leave(&pointer,0,13,&child_proxy);keymap_finish_pointer_leaves(0);
  assert(!map_count&&last[1]==3);
  before=packets;scrcpy_mapping_key(&s,&h,17,1);assert(packets==before);
  scrcpy_mapping_key(&s,&h,17,0);
  /* A key-flash repaint of a letterboxed task must not resize its hints to
   * the outer host frame and cancel a held joystick. A real fit resize must. */
  setup(&s,&h,KEYMAP_DPAD);s.p=&root;s.viewport=&s;s.hints=&h;
  tasks[0].width=448;tasks[0].height=704;
  struct game_fit_state repaint_fit={0};s.fit=&repaint_fit;
  s.fit_sw=448;s.fit_sh=704;s.fit_w=464;s.fit_h=824;s.bw=464;s.bh=824;
  struct game_fit_rect fit_rect=game_fit_area(&s);
  assert(fit_rect.x==0&&fit_rect.y==47&&fit_rect.width==464&&fit_rect.height==729);
  struct proxy repaint_surface={0},repaint_sub={0},repaint_callback={0},repaint_sync={0};
  h.piece.surface=&repaint_surface;h.piece.subsurface=&repaint_sub;
  h.piece.width=fit_rect.width;h.piece.height=fit_rect.height;h.dirty=1;
  real_marshal=repaint_marshal;real_listen=repaint_listen;real_destroy=repaint_destroy;
  get_version=repaint_version;real_display_flush=repaint_flush;
  csd_compositor=csd_subcompositor=csd_shm=&repaint_surface;
  csd_pool_i=&repaint_pool_i;csd_buffer_i=&repaint_buffer_i;
  keymap_display=(struct wl_display *)1;keymap_sync_proxy=&repaint_sync;
  keymap_event_queue=(struct wl_event_queue *)1;keymap_display_blocked=0;
  scrcpy_mapping_key(&s,&h,17,1);assert(map_count==1);
  for(int n=0;n<3;++n){
    before=packets;h.dirty=1;keymap_callback=&repaint_callback;
    keymap_done((void *)(uintptr_t)keymap_display_epoch,&repaint_callback,0);
    assert(map_count==1&&packets==before&&h.piece.width==464&&h.piece.height==729);
    assert(h.sync_x==0&&h.sync_y==47);
  }
  s.fit_w=440;h.dirty=1;keymap_callback=&repaint_callback;
  keymap_done((void *)(uintptr_t)keymap_display_epoch,&repaint_callback,0);
  assert(!map_count&&last[1]==3); /* genuine presentation resize still cancels */
  return 0;
}
