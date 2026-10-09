#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "android-window.c"
struct wl_proxy { const char *cls; };
static unsigned last_opcode, calls;
static struct wl_proxy *last_proxy;
static int forwarded_w, forwarded_h, keys;
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static uint32_t version(struct wl_proxy *p) {(void)p;return 1;}
static struct wl_proxy *send_request(struct wl_proxy *p,uint32_t op,const struct wl_interface *i,uint32_t v,uint32_t f,union wl_argument *a) {
 (void)i;(void)v;(void)f;(void)a;last_proxy=p;last_opcode=op;calls++;return NULL;
}
static void config(void *d,struct wl_proxy *p,int32_t w,int32_t h,struct wl_array *s) {(void)d;(void)p;(void)s;forwarded_w=w;forwarded_h=h;}
static void key(void *d,struct wl_proxy *p,uint32_t a,uint32_t b,uint32_t c,uint32_t e) {(void)d;(void)p;(void)a;(void)b;(void)c;(void)e;keys++;}
int main(void) {
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
 top_configure(NULL,&t,1292,764,&empty);
 assert(csd_visible() && host_w==1280 && host_h==720);
 union wl_argument geometry[4];csd_geometry(geometry);
 assert(geometry[0].i==-6 && geometry[1].i==-38 && geometry[2].i==1292 && geometry[3].i==764);
 manager=&t;decoration=&t;decoration_configure(NULL,&t,2);
 top_configure(NULL,&t,1000,600,&empty);
 assert(!csd_visible() && host_w==1000 && host_h==600);
 decoration_configure(NULL,&t,1);
 top_configure(NULL,&t,1012,644,&empty);
 assert(csd_visible() && host_w==1000 && host_h==600);
 state=1;top_configure(NULL,&t,1612,944,&states);
 assert(maximized && host_w==1600 && host_h==900);
 top_configure(NULL,&t,0,0,&empty);
 assert(!maximized && host_w==1000 && host_h==600);
 state=2;top_configure(NULL,&t,1920,1080,&states);
 assert(!csd_visible() && host_w==1920 && host_h==1080);
 top_configure(NULL,&t,0,0,&empty);
 assert(csd_visible() && host_w==1000 && host_h==600);
 /* Valid seat/serial for move and resize; controls never enter Android. */
 ime_seat=&keyboard;csd_part=0;csd_pieces[0].width=1012;csd_pieces[0].height=38;
 csd_x=100;csd_y=20;csd_button(42,0x110,1);assert(last_opcode==5 && last_proxy==&t);
 csd_x=2;csd_y=2;csd_button(43,0x110,1);assert(last_opcode==6);
 csd_x=920;csd_y=20;csd_button(44,0x110,1);csd_button(45,0x110,0);assert(last_opcode==13);
 csd_x=960;csd_button(46,0x110,1);csd_button(47,0x110,0);assert(last_opcode==9);
 csd_suppress=1;pointer_motion(NULL,&keyboard,0,0,0);pointer_axis(NULL,&keyboard,0,0,0);
 pointer_leave(NULL,&keyboard,0,&t);assert(csd_part==-1 && csd_suppress);
 manager=NULL;decoration=NULL;ime_seat=NULL;
 setenv("ANVILDROID_ANDROID_WINDOW","1",1);
 wl_proxy_marshal_array_flags(&t,0,NULL,1,1,NULL);assert(top==NULL && root_surface==NULL && !configured);
 wl_proxy_marshal_array_flags(&sub,0,NULL,1,1,NULL);assert(objects==NULL);
 puts("android-window: letterbox, input, fixed canvas, F11 and close lifecycle OK");
}
