/* Production event callbacks with a bounded fake Android sink. No display,
 * runtime or filesystem is opened by these focus/input isolation checks. */
#define ANVIL_KEYMAP_EDITOR 1
#define main mapping_baseline_main
#include "scrcpy-mapping.c"
#undef main

static int forwarded_keys,forwarded_enters,forwarded_leaves,forwarded_mods,forwarded_pointer,forwarded_touch;
static const struct interface surface_interface={.name="wl_surface"};
static struct proxy canvas={&surface_interface},panel={&surface_interface},overlay_surface={&surface_interface};
static struct object owner,keyboard,pointer;
static struct keymap_overlay draft;
static int destroyed_panel;
static struct proxy *panel_marshal(struct proxy *p,uint32_t op,union argument *a,const struct interface *i,uint32_t v) {
  (void)p;(void)op;(void)a;(void)i;(void)v;return 0;
}
static uint32_t panel_version(struct proxy *p) {(void)p;return 1;}
static void panel_destroy(struct proxy *p) {assert(p==&panel);++destroyed_panel;}
static void panel_enter(void *d,struct proxy *p,uint32_t serial,struct proxy *s,void *keys) {
  (void)d;(void)p;(void)serial;(void)s;(void)keys;++forwarded_enters;
}
static void panel_leave(void *d,struct proxy *p,uint32_t serial,struct proxy *s) {
  (void)d;(void)p;(void)serial;(void)s;++forwarded_leaves;
}
static void panel_key(void *d,struct proxy *p,uint32_t serial,uint32_t time,uint32_t key,uint32_t state) {
  (void)d;(void)p;(void)serial;(void)time;(void)key;(void)state;++forwarded_keys;
}
static void panel_mods(void *d,struct proxy *p,uint32_t s,uint32_t a,uint32_t b,uint32_t c,uint32_t g) {
  (void)d;(void)p;(void)s;(void)a;(void)b;(void)c;(void)g;++forwarded_mods;
}
static void pointer_enter(void *d,struct proxy *p,uint32_t serial,struct proxy *s,int32_t x,int32_t y) {
  (void)d;(void)p;(void)serial;(void)s;(void)x;(void)y;++forwarded_pointer;
}
static void pointer_leave(void *d,struct proxy *p,uint32_t serial,struct proxy *s) {
  (void)d;(void)p;(void)serial;(void)s;++forwarded_pointer;
}
static void pointer_motion(void *d,struct proxy *p,uint32_t t,int32_t x,int32_t y) {
  (void)d;(void)p;(void)t;(void)x;(void)y;++forwarded_pointer;
}
static void pointer_button(void *d,struct proxy *p,uint32_t s,uint32_t t,uint32_t b,uint32_t state) {
  (void)d;(void)p;(void)s;(void)t;(void)b;(void)state;++forwarded_pointer;
}
static void pointer_axis(void *d,struct proxy *p,uint32_t t,uint32_t a,int32_t v) {
  (void)d;(void)p;(void)t;(void)a;(void)v;++forwarded_pointer;
}
static void pointer_frame(void *d,struct proxy *p) {(void)d;(void)p;++forwarded_pointer;}
static void pointer_source(void *d,struct proxy *p,uint32_t s) {(void)d;(void)p;(void)s;++forwarded_pointer;}
static void pointer_stop(void *d,struct proxy *p,uint32_t t,uint32_t a) {(void)d;(void)p;(void)t;(void)a;++forwarded_pointer;}
static void pointer_discrete(void *d,struct proxy *p,uint32_t a,int32_t s) {(void)d;(void)p;(void)a;(void)s;++forwarded_pointer;}
static void touch_down(void *d,struct proxy *p,uint32_t s,uint32_t t,struct proxy *surface,int32_t id,int32_t x,int32_t y) {
  (void)d;(void)p;(void)s;(void)t;(void)surface;(void)id;(void)x;(void)y;++forwarded_touch;
}
static void touch_up(void *d,struct proxy *p,uint32_t s,uint32_t t,int32_t id) {
  (void)d;(void)p;(void)s;(void)t;(void)id;++forwarded_touch;
}
static void touch_motion(void *d,struct proxy *p,uint32_t t,int32_t id,int32_t x,int32_t y) {
  (void)d;(void)p;(void)t;(void)id;(void)x;(void)y;++forwarded_touch;
}
static void touch_cancel(void *d,struct proxy *p) {(void)d;(void)p;++forwarded_touch;}
static void (*keys[])(void)={0,(void(*)(void))panel_enter,(void(*)(void))panel_leave,(void(*)(void))panel_key,(void(*)(void))panel_mods,0};
static void (*pointers[])(void)={(void(*)(void))pointer_enter,(void(*)(void))pointer_leave,(void(*)(void))pointer_motion,(void(*)(void))pointer_button,
  (void(*)(void))pointer_axis,(void(*)(void))pointer_frame,(void(*)(void))pointer_source,(void(*)(void))pointer_stop,(void(*)(void))pointer_discrete};
static void setup_panel(void) {
  scrcpy_mapping_cancel();free(draft.history);
  memset(map_held,0,sizeof(map_held));memset(map_mouse_latched,0,sizeof(map_mouse_latched));
  map_fd=-1;packets=0;unavailable=0;absolute_pointer_enabled=0;
  forwarded_keys=forwarded_enters=forwarded_leaves=forwarded_mods=forwarded_pointer=forwarded_touch=0;
  owner=(struct object){.p=&canvas,.app=1,.has_bounds=1,.app_id="org.test",.bw=464,.bh=824};
  keyboard=(struct object){.listener=keys};pointer=(struct object){.listener=pointers};
  owner.next=&keyboard;keyboard.next=&pointer;objects=&owner;
  tasks[0]=(struct task){.id=5,.x=760,.y=154,.width=464,.height=824,.package="org.test"};task_count=1;
  draft=(struct keymap_overlay){.detached=1,.task=5,.package="org.test",.piece={.surface=&overlay_surface,.width=1000,.height=700},
    .panel={.piece={.surface=&panel},.width=320,.height=733},.model={.count=1,.selected=0,
      .points={{.key=30,.nx=32768,.ny=32768,.hold=1}}}};
  owner.overlay=&draft;
  destroyed_panel=0;real_marshal=panel_marshal;get_version=panel_version;real_destroy=panel_destroy;
}
static void focus(struct proxy *surface) {
  struct {size_t size,alloc;void *data;} empty={0};
  keymap_kenter(&keyboard,0,1,surface,&empty);
}
static void key(unsigned code,unsigned state) {keymap_key(&keyboard,0,2,2,code,state);}

static void test_shared_draft_and_binding(void) {
  setup_panel();focus(&canvas);
  assert(forwarded_enters==1&&keyboard.keyboard_focus==&owner);
  keymap_kleave(&keyboard,0,2,&canvas);focus(&panel);
  assert(keyboard.keyboard_focus==&owner&&keyboard.keymap_panel_focus&&keymap_owner(&panel)==&owner);
  assert(forwarded_enters==1&&forwarded_leaves==1&&owner.overlay==&draft);
  draft.binding_capture=1;draft.capture_field=KP_BIND;
  key(44,1);key(44,0);keymap_mods(&keyboard,0,3,1,0,0,0);
  assert(draft.model.points[0].key==44&&!draft.binding_capture&&draft.history_count>0);
  assert(!forwarded_keys&&!forwarded_mods&&!packets);
  struct keymap_model before=draft.model;int history=draft.history_count;
  keymap_kleave(&keyboard,0,4,&panel);keymap_finish_keyboard_leaves(0);
  assert(!keyboard.keyboard_focus&&!memcmp(&before,&draft.model,sizeof(before)));
  focus(&canvas);assert(!keyboard.keymap_panel_focus&&owner.overlay==&draft&&draft.history_count==history);
  assert(!memcmp(&before,&draft.model,sizeof(before))&&forwarded_enters==2&&forwarded_leaves==1);
}

static void test_panel_pointer_and_touch_never_reach_android(void) {
  setup_panel();focus(&panel);
  csd_pointer_enter(&pointer,0,1,&panel,100*256,200*256);
  assert(pointer.overlay_focus==&owner&&pointer.keymap_panel_pointer&&pointer.csd_suppress);
  draft.binding_capture=1;draft.capture_field=KP_BIND;
  csd_pointer_button(&pointer,0,2,2,273,1);csd_pointer_button(&pointer,0,3,3,273,0);
  assert(draft.model.points[0].source==KEYMAP_SOURCE_MOUSE&&draft.model.points[0].key==273&&!draft.binding_capture);
  csd_pointer_motion(&pointer,0,4,120*256,220*256);
  csd_pointer_axis(&pointer,0,5,0,256);csd_pointer_frame(&pointer,0);csd_pointer_source(&pointer,0,0);
  csd_pointer_stop(&pointer,0,5,0);csd_pointer_discrete(&pointer,0,0,1);
  void (*touches[])(void)={(void(*)(void))touch_down,(void(*)(void))touch_up,(void(*)(void))touch_motion,0,(void(*)(void))touch_cancel};
  struct object touch={.listener=touches};
  keymap_tdown(&touch,0,6,6,&panel,19,10,20);keymap_tmotion(&touch,0,7,19,30,40);keymap_tup(&touch,0,8,8,19);
  keymap_tdown(&touch,0,9,9,&panel,20,10,20);keymap_tcancel(&touch,0);
  csd_pointer_leave(&pointer,0,10,&panel);csd_pointer_frame(&pointer,0);
  assert(!forwarded_pointer&&!forwarded_touch&&!packets);
  /* Canvas entry is still forwarded, restoring HWC's real surface focus. */
  csd_pointer_enter(&pointer,0,11,&canvas,10,20);assert(forwarded_pointer==1&&!pointer.keymap_panel_pointer);
}

static void test_try_stays_on_canvas_and_external_blur_cancels(void) {
  setup_panel();draft.testing=1;focus(&canvas);key(30,1);
  assert(map_count==1&&last[1]==0);
  keymap_kleave(&keyboard,0,3,&canvas);focus(&panel);
  assert(!map_count&&last[1]==3&&draft.testing&&owner.overlay==&draft);
  int before=packets;key(30,0);key(30,1);key(30,0);key(45,1);key(45,0);
  assert(!map_count&&packets==before&&!forwarded_keys);
  /* Pointer input over the canvas cannot map while the tools own keyboard focus. */
  pointer.pointer_surface=&canvas;draft.model.points[0].source=KEYMAP_SOURCE_MOUSE;draft.model.points[0].key=273;
  struct object *target=0;assert(!map_pointer_hints(&pointer,&target));
  draft.model.points[0].source=KEYMAP_SOURCE_KEY;draft.model.points[0].key=30;
  keymap_kleave(&keyboard,0,4,&panel);focus(&canvas);key(30,1);
  assert(map_count==1);
  keymap_kleave(&keyboard,0,5,&canvas);keymap_finish_keyboard_leaves(0);
  assert(!map_count&&!keyboard.keyboard_focus&&last[1]==3&&draft.testing);
  /* Rotation metadata never changes the draft or turns the panel into a task. */
  struct keymap_model model=draft.model;
  assert(app_viewport_parse("1 2064 3264 2064 3264",&app_viewport));
  assert(keymap_owner(&panel)==&owner&&!find(&panel)&&!memcmp(&model,&draft.model,sizeof(model)));
  focus(&panel);key(1,1);key(1,0);assert(!draft.testing&&!forwarded_keys);
}

static void click_panel(struct keymap_rect rect) {
  pointer.csd_x=rect.x+2;pointer.csd_y=rect.y+33+2;
  csd_pointer_button(&pointer,0,10,10,272,1);csd_pointer_button(&pointer,0,11,11,272,0);
}
static void test_save_cancel_and_dock_boundaries(void) {
  for(int save=0;save<2;++save) {
    setup_panel();focus(&panel);csd_pointer_enter(&pointer,0,1,&panel,100*256,200*256);
    struct keymap_layout layout=keymap_layout(draft.panel.width,draft.panel.height-33,0);
    struct keymap_model before=draft.model;
    click_panel(save?layout.save:layout.cancel);
    assert(save?draft.save_pending: draft.close_pending&&draft.ack_pending==3);
    draft.binding_capture=1;draft.capture_field=KP_BIND;key(45,1);key(45,0);
    assert(!memcmp(&before,&draft.model,sizeof(before))&&!forwarded_keys&&!forwarded_pointer&&!packets);
  }
  setup_panel();focus(&panel);csd_pointer_enter(&pointer,0,1,&panel,100*256,200*256);
  keymap_history_init(&draft);struct keymap_model before=draft.model;
  struct keymap_model *history=draft.history;
  keymap_panel_close(&draft,0);assert(draft.panel.dock_pending);
  keymap_overlay_sync(&owner,0,0,1000,700);
  assert(!draft.detached&&!draft.panel.piece.surface&&owner.overlay==&draft&&destroyed_panel==1);
  assert(draft.history==history&&!memcmp(&before,&draft.model,sizeof(before)));
  /* Events already queued for the now-closed panel cannot bind or leak. */
  draft.binding_capture=1;draft.capture_field=KP_BIND;key(45,1);key(45,0);
  csd_pointer_leave(&pointer,0,12,&panel);csd_pointer_frame(&pointer,0);
  assert(!memcmp(&before,&draft.model,sizeof(before))&&!forwarded_keys&&!forwarded_pointer);
  focus(&canvas);assert(!keyboard.keymap_panel_focus&&owner.overlay==&draft);
}

int main(void) {
  test_shared_draft_and_binding();test_panel_pointer_and_touch_never_reach_android();
  test_try_stays_on_canvas_and_external_blur_cancels();
  test_save_cancel_and_dock_boundaries();
  free(draft.history);draft.history=0;objects=0;
  const char message[]="Detached panel focus, binding and Android input isolation: PASS\n";
  assert(write(1,message,sizeof(message)-1)==sizeof(message)-1);
  return 0;
}
