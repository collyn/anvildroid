#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
static char display_test_dir[]="/tmp/anvil-display-test-XXXXXX";
#define ANVIL_DISPLAY_DIRECTORY display_test_dir
#include "android-window.c"
static void acknowledge(int w,int h) {
  char path[512];snprintf(path,sizeof(path),"%s/display-state",display_test_dir);
  int pw=1280,ph=720,cw=pw,ch=ph;
  if((int64_t)pw*h>(int64_t)ph*w)cw=ph*w/h;else ch=pw*h/w;
  FILE *f=fopen(path,"w");assert(f);
  fprintf(f,"%d %d %d %d %d %d %d %d\n",w,h,pw,ph,(pw-cw)/2,(ph-ch)/2,cw,ch);fclose(f);
  display_size_refresh();fit(w,h);apply_size();
}
struct wl_proxy { const char *cls; };
static unsigned last_opcode, calls;
static struct wl_proxy *last_proxy;
static int forwarded_w, forwarded_h, keys;
static struct wl_proxy *crop_proxy;
static int crop_source[4],crop_dest[2];
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static uint32_t version(struct wl_proxy *p) {(void)p;return 1;}
static struct wl_proxy *send_request(struct wl_proxy *p,uint32_t op,const struct wl_interface *i,uint32_t v,uint32_t f,union wl_argument *a) {
 (void)i;(void)v;(void)f;
 if(p==crop_proxy&&op==1)for(int n=0;n<4;n++)crop_source[n]=a[n].i;
 if(p==crop_proxy&&op==2)for(int n=0;n<2;n++)crop_dest[n]=a[n].i;
 last_proxy=p;last_opcode=op;calls++;return NULL;
}
static void config(void *d,struct wl_proxy *p,int32_t w,int32_t h,struct wl_array *s) {(void)d;(void)p;(void)s;forwarded_w=w;forwarded_h=h;}
static void key(void *d,struct wl_proxy *p,uint32_t a,uint32_t b,uint32_t c,uint32_t e) {(void)d;(void)p;(void)a;(void)b;(void)c;(void)e;keys++;}
int main(void) {
 assert(mkdtemp(display_test_dir));
 struct wl_proxy root={"wl_surface"}, child={"wl_surface"}, sub={"wl_subsurface"}, t={"xdg_toplevel"}, keyboard={"wl_keyboard"};
 marshal=send_request;get_version=version;root_surface=&root;top=&t;
 remember(&sub,SUBSURFACE,&child,&root);
 fit(1600,720);assert(fit_w==1280 && fit_h==720 && pad_x==160 && pad_y==0);
 assert(input(&root,160*256,0)==0);assert(input(&child,640*256,0)==640*256);
 assert(!inside(&root,100*256,50*256));assert(inside(&root,160*256,50*256));
 fit(640,720);assert(fit_w==640 && fit_h==360 && pad_y==180);
 assert(input(&root,320*256,0)==640*256);assert(input(&root,360*256,1)==360*256);
 assert(input(&child,180*256,1)==360*256);
 struct tracked viewport={.surface=&root,.width=1280,.height=720};union wl_argument a[2];destination(&viewport,a);assert(a[0].i==640 && a[1].i==720);
 viewport.surface=&child;destination(&viewport,a);assert(a[0].i==640 && a[1].i==360);
 top_original[0]=(void(*)(void))config;
 uint32_t state=2;struct wl_array states={sizeof(state),sizeof(state),&state};
 top_configure(NULL,&t,1920,1080,&states);assert(fullscreen && forwarded_w==1280 && forwarded_h==720);
 fit(0,0);assert(host_w==1920 && host_h==1080);
 key_original[3]=(void(*)(void))key;focused=1;fullscreen=0;
 keyboard_key(NULL,&keyboard,1,2,87,1);assert(fullscreen_requested==1 && !fullscreen && last_opcode==11 && last_proxy==&t && keys==0);
 unsigned count=calls;keyboard_key(NULL,&keyboard,1,2,87,0);assert(calls==count && keys==0);
 keyboard_key(NULL,&keyboard,1,2,87,1);assert(fullscreen_requested==0 && last_opcode==12);
 focused=0;keyboard_key(NULL,&keyboard,1,2,87,1);assert(keys==1);
 /* No decoration manager (GNOME), compositor refusal, and accepted SSD. */
 fullscreen=0;fullscreen_requested=-1;
 struct wl_array empty={0,0,NULL};
 top_configure(NULL,&t,1280+2*CSD_BORDER,720+CSD_HEADER+CSD_BORDER,&empty);
 assert(csd_visible() && host_w==1280 && host_h==720);
 union wl_argument geometry[4];csd_geometry(geometry);
 assert(geometry[0].i==-CSD_BORDER && geometry[1].i==-CSD_HEADER && geometry[2].i==1280+2*CSD_BORDER && geometry[3].i==720+CSD_HEADER+CSD_BORDER);
 manager=&t;decoration=&t;decoration_configure(NULL,&t,2);
 top_configure(NULL,&t,1000,600,&empty);
 assert(!csd_visible() && host_w==1000 && host_h==600);
 decoration_configure(NULL,&t,1);
 top_configure(NULL,&t,1000+2*CSD_BORDER,600+CSD_HEADER+CSD_BORDER,&empty);
 assert(csd_visible() && host_w==1000 && host_h==600);
 state=1;top_configure(NULL,&t,1600+2*CSD_BORDER,900+CSD_HEADER+CSD_BORDER,&states);
 assert(maximized && host_w==1600 && host_h==900);
 top_configure(NULL,&t,0,0,&empty);
 assert(!maximized && host_w==1000 && host_h==600);
 state=2;top_configure(NULL,&t,1920,1080,&states);
 assert(!csd_visible() && host_w==1920 && host_h==1080);
 top_configure(NULL,&t,0,0,&empty);
 assert(csd_visible() && host_w==1000 && host_h==600);
 /* Valid seat/serial for move and resize; controls never enter Android. */
 ime_seat=&keyboard;csd_part=0;csd_pieces[0].width=1000+2*CSD_BORDER;csd_pieces[0].height=CSD_HEADER;
 csd_x=100;csd_y=20;csd_button(42,0x110,1);assert(last_opcode==5 && last_proxy==&t);
 csd_x=2;csd_y=2;csd_button(43,0x110,1);assert(last_opcode==6);
 csd_x=920;csd_y=20;csd_button(44,0x110,1);csd_button(45,0x110,0);assert(last_opcode==13);
 csd_x=960;csd_button(46,0x110,1);csd_button(47,0x110,0);assert(last_opcode==9);
 csd_suppress=1;pointer_motion(NULL,&keyboard,0,0,0);pointer_axis(NULL,&keyboard,0,0,0);
 pointer_leave(NULL,&keyboard,0,&t);assert(csd_part==-1 && csd_suppress);
 /* The custom-image adapter has its own frame: test its actual header entry,
  * submenu, fixed-canvas sizing, rotation and input suppression. */
 csd_part=0;csd_x=CSD_BUTTON/2;csd_y=20;
 csd_button(48,0x110,1);csd_button(49,0x110,0);
 assert(canvas_menu_open&&!canvas_menu_submenu);
 canvas_menu_activate(0);assert(canvas_menu_submenu);
 assert(canvas_menu_row(CANVAS_MENU_WIDTH+10,10)==1);
 canvas_menu_activate(1);acknowledge(480,800);
 assert(!canvas_menu_open&&host_w==480&&host_h==800&&canvas_w==1280&&canvas_h==720);
 canvas_menu_open=canvas_menu_submenu=1;canvas_menu_activate(5);acknowledge(800,480);
 assert(host_w==800&&host_h==480&&!canvas_menu_open);
 canvas_menu_open=canvas_menu_submenu=1;canvas_menu_activate(6);acknowledge(480,800);
 assert(host_w==480&&host_h==800);
 focused=1;canvas_menu_open=1;
 assert(canvas_menu_key(106,1)&&canvas_menu_submenu);
 assert(canvas_menu_key(106,0));
 assert(canvas_menu_key(1,1)&&canvas_menu_open&&!canvas_menu_submenu);
 assert(canvas_menu_key(1,0));
 assert(canvas_menu_key(1,1)&&!canvas_menu_open);
 assert(canvas_menu_key(1,0)); /* Escape release must not leak into Android. */
 fit(160,100);canvas_menu_open=canvas_menu_submenu=1;canvas_menu_offset=0;
 canvas_menu_scroll(1);assert(canvas_menu_offset==1);
 assert(canvas_menu_row(10,40)==2);
 canvas_menu_hover=1;canvas_menu_offset=0;
 assert(canvas_menu_key(103,1)&&canvas_menu_hover==7&&canvas_menu_offset==6);
 canvas_menu_key(103,0);
 uint32_t pixels[160*100]={0};canvas_menu_paint(pixels,160,100);
 assert(pixels[40*160+10]!=0&&pixels[99*160+159]==0);
 csd_x=150;csd_y=95;canvas_menu_focus=1;csd_suppress=1;
 canvas_menu_button(0x110,1);canvas_menu_button(0x110,0);
 assert(!canvas_menu_open); /* Outside cover dismisses without an Android tap. */
 fit(1000,600);maximized=1;canvas_menu_open=canvas_menu_submenu=1;
 canvas_menu_activate(1);acknowledge(480,800);assert(canvas_size_pending&&!maximized&&host_w==480);
 state=1;top_configure(NULL,&t,1600,900,&states);
 assert(canvas_size_pending&&host_w==480);
 top_configure(NULL,&t,1000,600,&empty);
 assert(!canvas_size_pending&&host_w==480&&host_h==800);
 /* Portrait must crop Android's physical letterbox, not fit the full
  * 1280x720 canvas inside a tall host window. Test real viewport and input. */
 struct wl_proxy vp_proxy={"wp_viewport"};
 remember(&child,SURFACE,NULL,NULL);remember(&vp_proxy,VIEWPORT,&child,NULL);
 struct tracked *vp=find(&vp_proxy);vp->width=1280;vp->height=720;
 vp->sx=vp->sy=0;vp->sw=1280*256;vp->sh=720*256;crop_proxy=&vp_proxy;
 canvas_menu_open=canvas_menu_submenu=1;canvas_menu_activate(3);
 assert(display_pending_w==720&&display_pending_h==1280);
 acknowledge(720,1280);
 assert(host_w==720&&host_h==1280&&fit_w==720&&fit_h==1280&&!pad_x&&!pad_y);
 assert(clip_viewport(vp));
 assert(crop_source[0]==437*256&&crop_source[1]==0&&crop_source[2]==405*256&&crop_source[3]==720*256);
 assert(crop_dest[0]==720&&crop_dest[1]==1280);
 assert(input(&root,0,0)==437*256&&input(&child,0,0)==437*256);
 assert(input(&child,720*256,0)==842*256&&input(&child,1280*256,1)==720*256);
 /* Invalid/stale state cannot crop live content. */
 char state_path[512];snprintf(state_path,sizeof(state_path),"%s/display-state",display_test_dir);
 FILE *state_file=fopen(state_path,"w");assert(state_file);
 fprintf(state_file,"720 1280 1280 720 1200 0 405 720\n");fclose(state_file);
 assert(!display_size_refresh()&&display_projection.x==437);
canvas_menu_open=canvas_menu_submenu=1;canvas_menu_activate(7);
 assert(canvas_custom_size.enabled&&!canvas_menu_open);
 state=3;top_configure(NULL,&t,902,1034,&states);
 assert(canvas_custom_size.dragging&&canvas_custom_size.w==900&&canvas_custom_size.h==1000);
 int cw,ch;
 for(int i=0;i<20;++i)assert(!custom_size_due(&canvas_custom_size,1280,1280,&cw,&ch));
 top_configure(NULL,&t,902,1034,&empty);
 for(int i=0;i<5;++i)assert(!custom_size_due(&canvas_custom_size,1280,1280,&cw,&ch));
 assert(custom_size_due(&canvas_custom_size,1280,1280,&cw,&ch)&&cw==900&&ch==1000);
 canvas_menu_open=canvas_menu_submenu=1;canvas_menu_activate(1);
 assert(!canvas_custom_size.enabled);

 setenv("ANVILDROID_ANDROID_WINDOW","1",1);
 wl_proxy_marshal_array_flags(&vp_proxy,0,NULL,1,1,NULL);
 wl_proxy_marshal_array_flags(&child,0,NULL,1,1,NULL);crop_proxy=NULL;
 manager=NULL;decoration=NULL;ime_seat=NULL;
 setenv("ANVILDROID_ANDROID_WINDOW","1",1);
 wl_proxy_marshal_array_flags(&t,0,NULL,1,1,NULL);assert(top==NULL && root_surface==NULL && !configured);
 wl_proxy_marshal_array_flags(&sub,0,NULL,1,1,NULL);assert(objects==NULL);
 puts("android-window: letterbox, input, Android projection crop, portrait input, F11 and close lifecycle OK");
}