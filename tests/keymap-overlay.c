#define ANVIL_CSD_EXPERIMENT 1
#define ANVIL_KEYMAP_OVERLAY_PROBE 1
#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int l,const char *t,const char *f,...){(void)l;(void)t;(void)f;return 0;}
static int forwarded,destroyed,commits;
static const struct interface surface_i={.name="wl_surface"},sub_i={.name="wl_subsurface"};
static struct proxy app={&surface_i},overlay={&surface_i},sub={&sub_i};
static struct proxy *marshal(struct proxy *p,uint32_t op,union argument *a,const struct interface *i,uint32_t v){
  (void)a;(void)i;(void)v;if(p==&app&&op==6)++commits;return 0;
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static void destroy(struct proxy *p){assert(p==&sub||p==&overlay);++destroyed;}
static void enter(void*d,struct proxy*p,uint32_t s,struct proxy*w,int32_t x,int32_t y){(void)d;(void)p;(void)s;(void)w;(void)x;(void)y;}
static void leave(void*d,struct proxy*p,uint32_t s,struct proxy*w){(void)d;(void)p;(void)s;(void)w;}
static void motion(void*d,struct proxy*p,uint32_t t,int32_t x,int32_t y){(void)d;(void)p;(void)t;(void)x;(void)y;++forwarded;}
static void button(void*d,struct proxy*p,uint32_t s,uint32_t t,uint32_t b,uint32_t state){(void)d;(void)p;(void)s;(void)t;(void)b;(void)state;++forwarded;}
int main(void){
  real_marshal=marshal;real_destroy=destroy;get_version=version;
  struct object s={.p=&app,.app=1,.app_id="com.android.settings"},pointer={0};
  s.next=&pointer;objects=&s;
  s.overlay=calloc(1,sizeof(*s.overlay));assert(s.overlay);
  s.overlay->piece=(struct csd_piece){.surface=&overlay,.subsurface=&sub,.width=800,.height=600};
  void(*callbacks[])(void)={(void(*)(void))enter,(void(*)(void))leave,(void(*)(void))motion,(void(*)(void))button};
  pointer.listener=callbacks;
  csd_pointer_enter(&pointer,0,1,&overlay,100*256,100*256);
  assert(pointer.overlay_focus==&s&&pointer.csd_suppress);
  csd_pointer_motion(&pointer,0,2,200*256,200*256);
  csd_pointer_button(&pointer,0,3,2,0x110,1);csd_pointer_button(&pointer,0,4,2,0x110,0);
  assert(!forwarded&&!destroyed);
  assert(keymap_add(&s.overlay->model,200,200,800,600));
  void(*keyboard_callbacks[])(void)={0,0,0,(void(*)(void))button,0,0};
  struct object keyboard={.keyboard_focus=&s,.listener=keyboard_callbacks};
  pointer.next=&keyboard;
  keymap_key(&keyboard,0,5,5,30,1);
  assert(s.overlay->model.points[0].key==30&&!forwarded);
  keymap_key(&keyboard,0,5,5,64,1); /* author WASD */
  assert(s.overlay->model.points[0].kind==KEYMAP_DPAD&&s.overlay->model.points[0].key==17);
  assert(!keymap_assign(&s.overlay->model,30)); /* WASD's trigger is fixed W */
  keymap_key(&keyboard,0,5,5,64,1);assert(s.overlay->model.points[0].kind==KEYMAP_SWIPE);
  keymap_key(&keyboard,0,5,5,65,1); /* endpoint follows editor pointer */
  assert(s.overlay->model.points[0].ex==keymap_normalize(200,800));
  keymap_key(&keyboard,0,5,5,66,1);assert(s.overlay->model.points[0].duration==500);
  keymap_key(&keyboard,0,5,5,67,1);assert(s.overlay->model.points[0].repeats==3); /* no meaningless swipe count */
  s.overlay->model.points[0].kind=KEYMAP_MACRO;
  keymap_key(&keyboard,0,5,5,67,1);assert(s.overlay->model.points[0].repeats==4);
  assert(keymap_valid(&s.overlay->model,1)&&!forwarded);
  csd_pointer_motion(&pointer,0,5,780*256,20*256);
  csd_pointer_button(&pointer,0,6,5,0x110,1);csd_pointer_button(&pointer,0,7,5,0x110,0);
  assert(s.overlay && s.overlay->close_pending);
  keymap_overlay_sync(&s,0,0,800,600);wayland_send(s.p,6,0);
  assert(!s.overlay&&s.overlay_dismissed&&destroyed==2&&commits==1&&!forwarded);
  keymap_key(&keyboard,0,8,8,30,0);assert(!forwarded); /* captured key-up cannot leak after close */
  csd_pointer_leave(&pointer,0,8,&overlay);
  csd_pointer_enter(&pointer,0,9,&app,100*256,100*256);
  csd_pointer_button(&pointer,0,10,9,0x110,1);assert(forwarded==1);
  /* Save completion and Escape must both tear down interception. Keep the
   * final editor key-up suppressed, then accept fresh content input. */
  for (int save=0;save<2;++save) {
    int before=forwarded;
    s.overlay=calloc(1,sizeof(*s.overlay));assert(s.overlay);
    s.overlay->piece=(struct csd_piece){.surface=&overlay,.subsurface=&sub,.width=800,.height=600};
    snprintf(s.overlay->nonce,sizeof(s.overlay->nonce),"session-%d",save);
    keymap_closed_nonce[0]=0;
    csd_pointer_enter(&pointer,0,11,&overlay,100*256,100*256);
    if(save) {
      /* Worker saved bytes, but Wayland has not removed the overlay yet. */
      s.overlay->close_pending=1;s.overlay->close_result=2;
    } else keymap_key(&keyboard,0,12,12,1,1);
    assert(!keymap_closed_nonce[0]);
    csd_pointer_button(&pointer,0,13,13,0x110,1);
    assert(forwarded==before);
    keymap_overlay_sync(&s,0,0,800,600);wayland_send(s.p,6,0);
    assert(!s.overlay&&keymap_closed_nonce[0]);
    assert(keymap_closed_result==(save?2:3));
    if(!save) {keymap_key(&keyboard,0,14,14,1,0);assert(forwarded==before);}
    csd_pointer_leave(&pointer,0,15,&overlay);
    csd_pointer_enter(&pointer,0,16,&app,100*256,100*256);
    assert(!pointer.csd_suppress&&!pointer.overlay_focus);
    csd_pointer_motion(&pointer,0,17,110*256,110*256);
    csd_pointer_button(&pointer,0,18,18,0x110,1);
    csd_pointer_button(&pointer,0,19,19,0x110,0);
    assert(forwarded==before+3);
  }
}
