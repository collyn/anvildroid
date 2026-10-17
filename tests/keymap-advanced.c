#define main mapping_baseline_main
#include "scrcpy-mapping.c"
#undef main
static void setup(struct object *s,struct keymap_overlay *h,int kind) {
  scrcpy_mapping_cancel();map_fd=-1;unavailable=0;
  *s=(struct object){.app=1,.has_bounds=1,.app_id="org.test",.bw=464,.bh=824};objects=s;
  tasks[0]=(struct task){.id=5,.x=760,.y=154,.width=464,.height=824,.package="org.test"};task_count=1;
  *h=(struct keymap_overlay){.passive=1,.task=5,.package="org.test",.model={.count=1,
    .points={{.key=17,.nx=32768,.ny=32768,.kind=kind,.ex=49152,.ey=32768,.duration=100,.repeats=3}}}};
  assert(keymap_valid(&h->model,1));
}
int main(void) {
  struct object s;struct keymap_overlay h;
  setup(&s,&h,KEYMAP_DPAD);
  assert(scrcpy_mapping_key(&s,&h,17,1)&&map_count==1);
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
  assert(map_count==10);scrcpy_mapping_cancel();assert(!map_count);
  return 0;
}
