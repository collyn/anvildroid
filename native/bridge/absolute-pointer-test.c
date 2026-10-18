#define socket test_socket
#define connect test_connect
#define send test_send
#define recv test_recv
#define getsockopt test_getsockopt
#include "bridge.c"
#include <assert.h>
#include <string.h>
static int packets, unavailable, packet_size;
static unsigned char last[32];
static void entered(void *d,struct proxy *p,uint32_t serial,struct proxy *s,void *keys) {
  (void)d;(void)p;(void)serial;(void)s;(void)keys;
}
static void left(void *d,struct proxy *p,uint32_t serial,struct proxy *s) {
  (void)d;(void)p;(void)serial;(void)s;
}
static struct object *dispatch_keyboard;
static struct proxy *dispatch_root,*dispatch_child;
static int dispatch_error;
static int nested_pending(struct wl_display *display) {
  (void)display;assert(dispatch_keyboard->keyboard_leave_pending&&map_count==1);return 0;
}
static int focus_dispatch(struct wl_display *display) {
  keymap_kleave(dispatch_keyboard,0,10,dispatch_root);
  if(dispatch_error)return -1;
  assert(!wl_display_dispatch_pending(display));
  assert(dispatch_keyboard->keyboard_leave_pending&&map_count==1);
  keymap_kenter(dispatch_keyboard,0,11,dispatch_child,0);
  return 0;
}
int __android_log_print(int l,const char*t,const char*f,...){(void)l;(void)t;(void)f;return 0;}
int __system_property_get(const char *key,char *value) {
  return snprintf(value,128,"%d",strstr(key,"width")?1984:1164);
}
int test_socket(int domain,int type,int protocol){(void)domain;(void)type;(void)protocol;return 999;}
int test_getsockopt(int fd,int level,int name,void *value,unsigned *size) {
  (void)fd;(void)level;assert(name==17&&*size==12);
  int *peer=value;peer[0]=123;peer[1]=2000;peer[2]=2000;return 0;
}
int test_connect(int fd,const void *address,unsigned length) {
  (void)fd;
  const char *p=address;
  assert(length==2+1+strlen("scrcpy_00000000"));
  assert(!p[2]&&!memcmp(p+3,"scrcpy_00000000",length-3));
  return unavailable?-1:0;
}
long test_recv(int fd,void *data,size_t size,int flags) {
  (void)fd;(void)flags;assert(size==1);*(char*)data=0;return 1;
}
long test_send(int fd,const void *data,size_t size,int flags) {
  (void)fd;(void)flags;assert(size==32||size==21);memcpy(last,data,size);packet_size=(int)size;++packets;return size;
}
int main(void) {
  struct object s={.app=1,.has_bounds=1,.app_id="org.test"};objects=&s;
  tasks[0]=(struct task){.id=5,.x=760,.y=154,.width=464,.height=824,.package="org.test"};task_count=1;
  struct keymap_overlay h={.passive=1,.task=5,.package="org.test",.model={.count=2,.points={{.key=30,.nx=0,.ny=0},{.key=17,.nx=65535,.ny=65535}}}};
  assert(scrcpy_mapping_key(&s,&h,30,1)==1&&packets==1&&map_count==1);
  assert(last[10]==0&&last[11]==0&&last[12]==2&&last[13]==248);
  assert(scrcpy_mapping_key(&s,&h,17,1)==1&&packets==2&&last[9]==1);
  assert(scrcpy_mapping_key(&s,&h,30,0)==1&&packets==3&&last[9]==0);
  assert(scrcpy_mapping_key(&s,&h,17,1)==1&&packets==3);
  assert(scrcpy_mapping_key(&s,&h,17,0)==1&&packets==4&&last[9]==1&&map_count==0);
  scrcpy_mapping_key(&s,&h,30,1);scrcpy_mapping_cancel();
  assert(last[1]==3&&map_count==0);
  int before=packets;h.task=6;
  assert(scrcpy_mapping_key(&s,&h,30,1)==1&&packets==before);
  scrcpy_mapping_key(&s,&h,30,0);h.task=5;map_fd=-1;unavailable=1;
  assert(scrcpy_mapping_key(&s,&h,30,1)==1&&packets==before&&map_count==0);
  assert(scrcpy_mapping_key(&s,&h,31,1)==0);
  /* Full UI retains the Waydroid host identity, but injects into its app
   * task in Android coordinates, independent of host fit/letterboxing. */
  scrcpy_mapping_key(&s,&h,30,0);unavailable=0;map_fd=-1;
  s.app=0;s.full_ui=1;s.app_id="Waydroid";
  s.full_canvas_w=1984;s.full_canvas_h=1164;s.full_w=992;s.full_h=700;
  tasks[1]=(struct task){.id=1,.package="com.android.launcher3",.width=1984,.height=1164};
  task_count=2;
  assert(scrcpy_mapping_key(&s,&h,30,1)==1&&packets==before+1);
  assert(last[10]==0&&last[11]==0&&last[12]==2&&last[13]==248);
  assert(last[14]==0&&last[15]==0&&last[16]==0&&last[17]==154);
  assert(scrcpy_mapping_key(&s,&h,30,0)==1&&map_count==0);
  tasks[0].id=9;
  before=packets;
  assert(scrcpy_mapping_key(&s,&h,30,1)==1&&packets==before);
  /* Android16 must use absolute display coordinates, not accelerated REL
   * deltas. Parent and child events must agree even at nonzero task origins. */
  s.full_ui=0;s.app=1;s.app_id="org.test";
  struct proxy root={0},child={0},sub_proxy={0};
  s.p=&root;s.viewport=&s;scale_has_viewporter=1;objects=&s;
  tasks[0].id=5;task_count=1;map_fd=-1;absolute_pointer_enabled=1;
  struct object pointer={.pointer_surface=&root};
  assert(absolute_pointer_update(&pointer,810*256,234*256,1));
  assert(pointer.absolute_x==810&&pointer.absolute_y==234&&last[1]==7&&packet_size==32);
  assert(absolute_pointer_button(&pointer,0x110,1)&&last[1]==0&&pointer.absolute_buttons==1);
  assert(absolute_pointer_update(&pointer,820*256,244*256,1)&&last[1]==2);
  absolute_pointer_leave(&pointer);assert(last[1]==1&&!pointer.absolute_buttons&&!pointer.absolute_valid);
  struct object layer={.p=&child};
  struct object sublayer={.p=&sub_proxy,.protocol_name="wl_subsurface",.surface=&layer,.parent=&s,.x=760,.y=154};
  s.next=&sublayer;sublayer.next=&layer;pointer.pointer_surface=&child;
  assert(absolute_pointer_update(&pointer,50*256,80*256,0));
  assert(pointer.absolute_x==810&&pointer.absolute_y==234);
  assert(absolute_pointer_axis(&pointer,0,10*256)&&packet_size==21);
  assert(last[15]==0xf8&&last[16]==0); /* wheel down -> Android -1 */
  before=packets;map_count=1;
  absolute_pointer_update(&pointer,51*256,81*256,1);
  assert(packets==before); /* No mouse hover mixed into active keymap fingers. */
  map_count=0;absolute_pointer_enabled=0;
  assert(!absolute_pointer_update(&pointer,1,1,1));
  /* A compositor click can move keyboard focus from the app root to its
   * content subsurface. Preserve the held aim and consume fire in that pair. */
  void (*listeners[])(void)={0,(void (*)(void))entered,(void (*)(void))left};
  struct object keyboard={.keyboard_focus=&s,.listener=listeners};
  layer.next=&keyboard;
  struct keymap_overlay aim={.passive=1,.task=5,.package="org.test",
    .model={.count=1,.points={{.key=36,.nx=32000,.ny=32000,.ex=48000,.ey=48000,.kind=KEYMAP_AIM}}}};
  s.hints=&aim;keyboard.keymap_blocked_keys[1]=16;
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  keymap_kleave(&keyboard,0,1,&root);
  assert(keyboard.keyboard_leave_pending&&map_count==1);
  before=packets;
  assert(absolute_pointer_update(&pointer,60*256,90*256,1)&&packets==before);
  keymap_kenter(&keyboard,0,2,&child,0);
  keymap_finish_keyboard_leaves(0);
  assert(keyboard.keyboard_focus==&s&&!keyboard.keyboard_leave_pending&&map_count==1);
  assert(keyboard.keymap_blocked_keys[1]==16);
  assert(absolute_pointer_button(&pointer,0x110,1)&&map_count==2);
  assert(absolute_pointer_button(&pointer,0x110,0)&&map_count==1);
  /* Bridge-created chrome and hint surfaces are not in objects, but keep
   * their app keyboard owner and must not cancel a held control on transfer. */
  struct proxy header_proxy={0},menu_proxy={0},frame_bar_proxy={0},fit_bar_proxy={0},hint_proxy={0};
  struct csd_frame owned_frame={0};struct game_fit_state owned_fit={0};
  owned_frame.pieces[0].surface=&header_proxy;owned_frame.menu.surface=&menu_proxy;
  owned_frame.bars[0].surface=&frame_bar_proxy;owned_fit.bars[0].surface=&fit_bar_proxy;
  s.csd=&owned_frame;s.fit=&owned_fit;aim.piece.surface=&hint_proxy;
  struct proxy *owned_surfaces[]={&header_proxy,&menu_proxy,&frame_bar_proxy,&fit_bar_proxy,&hint_proxy};
  struct proxy *previous=&child;
  for(unsigned i=0;i<sizeof(owned_surfaces)/sizeof(owned_surfaces[0]);++i){
    assert(!find(owned_surfaces[i])&&keymap_owner(owned_surfaces[i])==&s);
    keymap_kleave(&keyboard,0,2,previous);
    keymap_kenter(&keyboard,0,2,owned_surfaces[i],0);
    keymap_finish_keyboard_leaves(0);
    assert(keyboard.keyboard_focus==&s&&map_count==1&&map_held[36]);previous=owned_surfaces[i];
  }
  keymap_kleave(&keyboard,0,2,previous);keymap_kenter(&keyboard,0,2,&child,0);
  assert(map_count==1);s.csd=0;s.fit=0;aim.piece.surface=0;
  assert(!keymap_owner(&header_proxy)&&!keymap_owner(&fit_bar_proxy)&&!keymap_owner(0));
  /* A genuine leave is finalized at the dispatch boundary, only for the
   * dispatched connection. Same-package replacement objects are not owners. */
  keymap_kleave(&keyboard,0,3,&child);
  keymap_finish_keyboard_leaves((struct wl_display *)1);
  assert(keyboard.keyboard_leave_pending&&map_count==1);
  keymap_finish_keyboard_leaves(0);
  assert(!keyboard.keyboard_focus&&!map_count&&!keyboard.keymap_blocked_keys[1]);
  struct proxy replacement_proxy={0};
  struct object replacement={.p=&replacement_proxy,.app=1,.app_id="org.test"};
  keyboard.keyboard_focus=&s;
  before=packets;scrcpy_mapping_key(&s,&aim,36,1);assert(!map_count&&packets==before);
  scrcpy_mapping_key(&s,&aim,36,0);
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  keyboard.next=&replacement;
  keymap_kleave(&keyboard,0,4,&root);
  keymap_kenter(&keyboard,0,5,&replacement_proxy,0);
  assert(keyboard.keyboard_focus==&replacement&&!map_count);
  keyboard.next=0;keyboard.keyboard_focus=&s;
  scrcpy_mapping_key(&s,&aim,36,0);
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  keymap_kleave(&keyboard,0,6,&root);
  uint32_t held_key=36;
  struct { size_t size,alloc;void *data; } held={sizeof(held_key),sizeof(held_key),&held_key};
  keymap_kenter(&keyboard,0,7,&root,&held);
  assert(keyboard.keyboard_focus==&s&&!map_count);
  assert(keyboard.keymap_blocked_keys[1]&(1u<<4));
  keymap_key(&keyboard,0,8,0,36,1);assert(!map_count);
  keymap_key(&keyboard,0,9,0,36,0);assert(!map_count&&!keyboard.keymap_blocked_keys[1]);
  keymap_key(&keyboard,0,10,0,36,1);assert(map_count==1);
  keymap_key(&keyboard,0,11,0,36,0);assert(!map_count);
  /* Key-up while another client has focus is not delivered here. A valid
   * empty enter snapshot proves release and admits the first fresh press. */
  struct { size_t size,alloc;void *data; } empty_keys={0,0,0};
  keyboard.keyboard_focus=&s;
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  keymap_kleave(&keyboard,0,12,&root);keymap_finish_keyboard_leaves(0);
  assert(!map_count&&map_held[36]);
  keymap_kenter(&keyboard,0,13,&root,&empty_keys);assert(!map_held[36]);
  keymap_key(&keyboard,0,14,0,36,1);assert(map_count==1);
  /* Coalescing a same-owner child transfer must preserve the held registry. */
  keymap_kleave(&keyboard,0,15,&root);keymap_kenter(&keyboard,0,16,&child,&empty_keys);
  assert(map_count==1&&map_held[36]);
  keymap_key(&keyboard,0,17,0,36,0);assert(!map_count);
  /* Enter listing the activation key keeps it suppressed until physical UP. */
  scrcpy_mapping_key(&s,&aim,36,1);keymap_kleave(&keyboard,0,18,&child);
  keymap_finish_keyboard_leaves(0);keymap_kenter(&keyboard,0,19,&root,&held);
  keymap_key(&keyboard,0,20,0,36,1);assert(!map_count&&map_held[36]);
  keymap_key(&keyboard,0,21,0,36,0);keymap_key(&keyboard,0,22,0,36,1);assert(map_count==1);
  keymap_key(&keyboard,0,23,0,36,0);
  /* A malformed enter array cannot clear a previously held key. */
  scrcpy_mapping_key(&s,&aim,36,1);keymap_kleave(&keyboard,0,24,&root);
  keymap_finish_keyboard_leaves(0);
  struct { size_t size,alloc;void *data; } invalid_keys={1,1,&held_key};
  keymap_kenter(&keyboard,0,25,&root,&invalid_keys);assert(map_held[36]);
  uint32_t invalid_key=768;invalid_keys.size=invalid_keys.alloc=sizeof(invalid_key);invalid_keys.data=&invalid_key;
  keymap_kenter(&keyboard,0,25,&root,&invalid_keys);assert(map_held[36]);
  keymap_key(&keyboard,0,26,0,36,1);assert(!map_count);
  keymap_key(&keyboard,0,27,0,36,0);keymap_key(&keyboard,0,28,0,36,1);assert(map_count==1);
  keymap_key(&keyboard,0,29,0,36,0);
  /* Unexpected button input between leave/enter must cancel, never fall
   * through to the legacy mouse sink; its matching release is consumed. */
  keyboard.keyboard_focus=&s;
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  keymap_kleave(&keyboard,0,6,&root);
  before=packets;
  assert(absolute_pointer_button(&pointer,0x110,1)&&!map_count);
  assert(packets==before+1&&last[1]==3&&pointer.focus_blocked_button==0x110);
  before=packets;
  assert(absolute_pointer_button(&pointer,0x110,0)&&packets==before&&!pointer.focus_blocked_button);
  /* A true pointer leave cancels by the dispatch boundary; physical key-up
   * is required before a held key can start a fresh contact. */
  scrcpy_mapping_key(&s,&aim,36,0);keyboard.keyboard_focus=&s;
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  absolute_pointer_leave(&pointer);assert(map_count==1);
  keyboard.next=&pointer;keymap_finish_pointer_leaves(0);keyboard.next=0;
  assert(!map_count&&last[1]==3);
  before=packets;scrcpy_mapping_key(&s,&aim,36,1);assert(!map_count&&packets==before);
  scrcpy_mapping_key(&s,&aim,36,0);
  /* Nested dispatch must not finalize the outer batch's leave, whereas an
   * outer dispatch failure must finalize it before returning the error. */
  keyboard.keyboard_focus=&s;keyboard.display=(struct wl_display *)1;
  dispatch_keyboard=&keyboard;dispatch_root=&root;dispatch_child=&child;
  real_dispatch=focus_dispatch;real_dispatch_pending=nested_pending;
  assert(scrcpy_mapping_key(&s,&aim,36,1)&&map_count==1);
  assert(!wl_display_dispatch(keyboard.display)&&map_count==1&&!keyboard.keyboard_leave_pending);
  dispatch_error=1;
  assert(wl_display_dispatch(keyboard.display)==-1&&!map_count&&!keyboard.keyboard_focus);
}
