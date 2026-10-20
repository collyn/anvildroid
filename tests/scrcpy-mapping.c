#define socket test_socket
#define connect test_connect
#define send test_send
#define recv test_recv
#define getsockopt test_getsockopt
#include "../native/bridge/bridge.c"
#include <assert.h>
#include <string.h>
static int packets, unavailable, packet_size;
static unsigned char last[32];
static unsigned char sequence[4096][32];
static int sequence_count;
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
  (void)fd;(void)flags;assert(size==32||size==21);memcpy(last,data,size);if(sequence_count<4096){memset(sequence[sequence_count],0,32);memcpy(sequence[sequence_count++],data,size);}packet_size=(int)size;++packets;return unavailable==2?(long)size-1:(long)size;
}
static void keyboard_leave(void *data,struct proxy *proxy,uint32_t serial,struct proxy *surface) {
  (void)data;(void)proxy;(void)serial;(void)surface;
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
  scrcpy_mapping_key(&s,&h,30,0);tasks[0].id=5;task_count=1;map_fd=-1;absolute_pointer_enabled=1;
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
  /* A new/empty profile has no passive hints. Try still releases a held
   * contact on keyboard-only focus loss, with the pointer staying put. */
  scrcpy_mapping_cancel();memset(map_held,0,sizeof(map_held));
  struct keymap_overlay draft={.testing=1,.task=5,.package="org.test",.model={.count=1,
    .points={{.key=30,.nx=32768,.ny=32768,.hold=1}}}};
  s.overlay=&draft;assert(!s.hints);
  void (*keyboard_callbacks[])(void)={0,0,(void (*)(void))keyboard_leave};
  struct object keyboard={.keyboard_focus=&s,.listener=keyboard_callbacks};
  layer.next=&keyboard;
  assert(scrcpy_mapping_key(&s,&draft,30,1)&&map_count==1&&last[1]==0);
  keymap_kleave(&keyboard,0,10,&root);
  assert(map_count==1&&keyboard.keyboard_leave_pending);
  keymap_finish_keyboard_leaves(0);
  assert(!keyboard.keyboard_focus&&!map_count&&last[1]==3);
  before=packets;map_advanced_advance(100);
  assert(!map_count&&packets==before);
  return 0;
}
