#define main mapping_baseline_main
#include "scrcpy-mapping.c"
#undef main

static struct proxy relative_proxy={&keymap_relative_interface};
static struct proxy lock_proxy={&keymap_locked_interface};
static struct proxy relative_manager_proxy={&keymap_relative_manager_interface};
static struct proxy constraints_proxy={&keymap_constraints_interface};
static struct interface pointer_iface={"wl_pointer",5,0,0,0,0};
static struct interface surface_iface={"wl_surface",4,0,0,0,0};
static int constructor_fail,listen_fail,destroyed,hidden,restored;
static union argument last_cursor[4];
static uint32_t capture_version(struct proxy *p){(void)p;return 1;}
static struct proxy *capture_marshal(struct proxy *p,uint32_t op,union argument *a,
    const struct interface *iface,uint32_t version) {
  (void)version;
  if(iface==&keymap_relative_interface) {
    assert(p==&relative_manager_proxy&&op==1&&a[1].o);
    return constructor_fail==1?0:&relative_proxy;
  }
  if(iface==&keymap_locked_interface) {
    assert(p==&constraints_proxy&&op==1&&a[1].o&&a[2].o&&!a[3].o&&a[4].u==1);
    return constructor_fail==2?0:&lock_proxy;
  }
  if(p->interface==&pointer_iface&&op==0) {
    memcpy(last_cursor,a,sizeof(last_cursor));
    if(a[1].o)++restored;else ++hidden;
  }
  return 0;
}
static int capture_listen(struct proxy *p,void (**listener)(void),void *data) {
  (void)listener;(void)data;return listen_fail&&p==&lock_proxy;
}
static void capture_destroy(struct proxy *p){assert(p);++destroyed;}
static struct object owner,pointer,keyboard,cursor;
static struct proxy owner_proxy={&surface_iface},pointer_proxy={&pointer_iface},cursor_proxy={&surface_iface};
static struct proxy fallback_proxy={&surface_iface};
static struct keymap_overlay profile;
static void capture_setup(void) {
  scrcpy_mapping_cancel();map_fd=-1;unavailable=0;memset(map_held,0,sizeof(map_held));
  memset(map_mouse_latched,0,sizeof(map_mouse_latched));
  constructor_fail=listen_fail=destroyed=hidden=restored=0;
  owner=(struct object){.p=&owner_proxy,.protocol_name="wl_surface",.display=(void *)17,
    .app=1,.has_bounds=1,.app_id="org.test",.bw=464,.bh=824};
  owner.viewport=&owner;scale_has_viewporter=1;
  pointer=(struct object){.p=&pointer_proxy,.protocol_name="wl_pointer",.display=(void *)17,
    .pointer_surface=&owner_proxy,.cursor_serial=77,.cursor_surface=&cursor_proxy,.cursor_requested=1,
    .cursor_hotspot_x=3,.cursor_hotspot_y=4};
  keyboard=(struct object){.keyboard_focus=&owner,.display=(void *)17};
  cursor=(struct object){.p=&cursor_proxy,.protocol_name="wl_surface",.display=(void *)17,
    .committed_attached=1};
  owner.next=&pointer;pointer.next=&keyboard;keyboard.next=&cursor;objects=&owner;
  tasks[0]=(struct task){.id=5,.x=760,.y=154,.width=464,.height=824,.package="org.test"};task_count=1;
  profile=(struct keymap_overlay){.passive=1,.task=5,.package="org.test",.model={.count=1}};
  profile.model.points[0]=(struct keymap_point){.key=38,.nx=32768,.ny=32768};
  keymap_set_kind(&profile.model.points[0],KEYMAP_LOOK);profile.model.points[0].activation=1;
  owner.hints=&profile;
  assert(keymap_valid(&profile.model,1));
  real_marshal=capture_marshal;real_listen=capture_listen;real_destroy=capture_destroy;get_version=capture_version;
  real_set_queue=0;real_display_flush=0;
  if(!keymap_capture_bindings)keymap_capture_bindings=calloc(1,sizeof(*keymap_capture_bindings));
  *keymap_capture_bindings=(struct keymap_capture_binding){.display=(void *)17,
    .relative_manager=&relative_manager_proxy,.constraints=&constraints_proxy,.relative_name=7,.constraints_name=8};
}
int main(void) {
  capture_setup();int before=packets;
  assert(keymap_capture_status(&owner)==1);
  assert(scrcpy_mapping_key(&owner,&profile,38,1));
  assert(keymap_capture_status(&owner)==2&&map_count==1&&packets==before);
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,256,0);
  assert(packets==before); /* Request and relative events before ack inject nothing. */
  keymap_locked(0,&lock_proxy);
  assert(keymap_capture_status(&owner)==3&&packets==before+1&&last[1]==0&&hidden==1);
  int initial_x=map_advanced[0].lx;
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);
  assert(last[1]==2&&map_advanced[0].lx>initial_x); /* Unaccelerated delta. */
  scrcpy_mapping_key(&owner,&profile,38,0);assert(keymap_capture_active(&owner));
  /* Source cursor requests during capture stay hidden and retain restore state. */
  union argument cursor_args[4]={{.u=77},{.o=&cursor_proxy},{.i=9},{.i=11}};
  keymap_capture_cursor_request(&pointer,cursor_args);assert(!cursor_args[1].o);
  assert(scrcpy_mapping_escape()&&!keymap_capture.owner&&!map_count&&last[1]==3);
  assert(destroyed==2&&restored==1&&last_cursor[2].i==9&&last_cursor[3].i==11);
  before=packets;keymap_locked(0,&lock_proxy);
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);assert(packets==before);

  capture_setup();pointer.cursor_requested=0;before=packets;
  scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  assert(packets==before+1&&!hidden); /* Never hide a cursor with unknown restore state. */
  scrcpy_mapping_cancel();assert(!restored);

  /* The bridge's fallback exists before any guest cursor request. Capture
   * must hide it on grant, then restore it on escape and compositor unlock. */
  for(int unlock=0;unlock<2;++unlock) {
    capture_setup();pointer.cursor_requested=0;pointer.cursor_surface=0;
    pointer.edit_cursor=&fallback_proxy;
    csd_cursor_show(&pointer);assert(restored==1&&last_cursor[1].o==&fallback_proxy);
    restored=0;
    scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
    assert(hidden==1&&!last_cursor[1].o);
    if(unlock)keymap_unlocked(0,&lock_proxy);else assert(scrcpy_mapping_escape());
    assert(restored==1&&last_cursor[1].o==&fallback_proxy&&
      last_cursor[2].i==3&&last_cursor[3].i==2);
    pointer.edit_cursor=0;
  }

  /* Enter serial zero remains valid after wraparound, including capture. */
  capture_setup();pointer.cursor_serial=0;
  scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  assert(hidden==1&&!last_cursor[0].u);
  assert(scrcpy_mapping_escape()&&restored==1&&!last_cursor[0].u);

  capture_setup();before=packets;
  keymap_capture_bindings->constraints=0;
  scrcpy_mapping_key(&owner,&profile,38,1);assert(!map_count&&packets==before&&!keymap_capture.owner);
  for(int failure=1;failure<=2;++failure) {
    capture_setup();constructor_fail=failure;before=packets;
    scrcpy_mapping_key(&owner,&profile,38,1);assert(!map_count&&packets==before&&!keymap_capture.owner);
  }
  capture_setup();listen_fail=1;before=packets;
  scrcpy_mapping_key(&owner,&profile,38,1);assert(!map_count&&packets==before&&!keymap_capture.owner);

  capture_setup();before=packets;
  scrcpy_mapping_key(&owner,&profile,38,1);keymap_capture.requested-=3000;
  map_advanced_advance(50);assert(!keymap_capture.owner&&!map_count&&packets==before+1);

  capture_setup();scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  keyboard.keyboard_focus=0;before=packets;
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);
  assert(!map_count&&!keymap_capture.owner&&packets==before+1&&last[1]==3);

  capture_setup();scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  keymap_unlocked(0,&lock_proxy);assert(!map_count&&!keymap_capture.owner&&restored==1);
  /* Physical held key cannot reactivate after unlock/refocus without key-up. */
  before=packets;scrcpy_mapping_key(&owner,&profile,38,1);assert(packets==before&&!map_count);
  scrcpy_mapping_key(&owner,&profile,38,0);scrcpy_mapping_key(&owner,&profile,38,1);
  assert(map_count==1&&keymap_capture_status(&owner)==2);
  keymap_capture_forget(&pointer);assert(!map_count&&!keymap_capture.owner);

  capture_setup();scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  struct object registry={.display=(void *)17};
  assert(keymap_capture_remove(&registry,7)&&!keymap_capture.owner&&!map_count);
  assert(!keymap_capture_supported(&owner));
  keymap_capture_disconnect((void *)17);assert(!keymap_capture_bindings);
  /* Suspend releases the contact and lock; resume requires a fresh grant. */
  capture_setup();profile.model.points[0].suspend_key=56;
  scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  scrcpy_mapping_key(&owner,&profile,56,1);
  assert(map_count==1&&!map_advanced[0].down&&map_advanced[0].suspended&&!keymap_capture.owner);
  before=packets;keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);assert(packets==before);
  assert(!map_pointer_profile(&pointer));
  scrcpy_mapping_key(&owner,&profile,56,0);assert(keymap_capture_status(&owner)==2&&packets==before);
  keymap_locked(0,&lock_proxy);assert(keymap_capture_active(&owner)&&last[1]==0);
  scrcpy_mapping_key(&owner,&profile,56,1);keyboard.keyboard_focus=0;
  map_advanced_advance(1);assert(!map_count&&!keymap_capture.owner);
  before=packets;scrcpy_mapping_key(&owner,&profile,56,0);assert(!map_count&&packets==before);
  /* Relative drag-shot owns each delta once, then camera resumes at center. */
  capture_setup();profile.model.count=2;
  profile.model.points[1]=(struct keymap_point){.key=272,.nx=45000,.ny=45000};
  keymap_set_kind(&profile.model.points[1],KEYMAP_BASIC);
  profile.model.points[1].source=KEYMAP_SOURCE_MOUSE;profile.model.points[1].end_mode=KEYMAP_END_HOLD;
  profile.model.points[1].drag_shot=1;
  assert(keymap_valid(&profile.model,1));
  scrcpy_mapping_key(&owner,&profile,38,1);keymap_locked(0,&lock_proxy);
  int handoff=sequence_count;
  absolute_pointer_button(&pointer,272,1);assert(map_count==2&&map_drag_slot==1&&!map_advanced[0].down);
  assert(sequence[handoff][1]==1&&sequence[handoff][9]==0);
  assert(sequence[handoff+1][1]==0&&sequence[handoff+1][9]==1);
  initial_x=map_advanced[1].lx;before=packets;
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);
  assert(packets==before+1&&map_advanced[1].lx>initial_x&&!map_advanced[0].down);
  absolute_pointer_button(&pointer,272,0);
  assert(map_count==1&&map_drag_slot==-1&&map_advanced[0].down&&map_advanced[0].lx==32768);
  absolute_pointer_button(&pointer,272,1);keyboard.keyboard_focus=0;
  keymap_relative_motion(0,&relative_proxy,0,0,256,0,512,0);
  assert(!map_count&&map_drag_slot==-1&&!keymap_capture.owner);
  before=packets;absolute_pointer_button(&pointer,272,0);assert(packets==before);
  return 0;
}
