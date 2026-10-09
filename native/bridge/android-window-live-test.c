/* Real compositor protocol smoke for the production single-display adapter. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include "android-window.c"
extern const struct wl_interface xdg_wm_base_interface,xdg_surface_interface,xdg_toplevel_interface;
extern const struct wl_interface wp_viewporter_interface,wp_viewport_interface;
static void *library;
static struct wl_proxy *shell;
static struct wl_proxy *viewporter;
static int acks;
static int (*roundtrip)(struct wl_proxy *);
static void await_state(struct wl_proxy *display,int want_max,int want_full) {
 for(int i=0;i<100;i++) {
   assert(roundtrip(display)>=0);
   if(maximized==want_max && fullscreen==want_full)return;
   usleep(10000);
 }
 assert(!"Compositor did not confirm the requested window state");
}
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static struct wl_proxy *request(struct wl_proxy *p,uint32_t op,const struct wl_interface *i,union wl_argument *a) {
 return wl_proxy_marshal_array_flags(p,op,i,1,0,a);
}
static void ping(void *d,struct wl_proxy *p,uint32_t serial){(void)d;union wl_argument a={.u=serial};request(p,3,NULL,&a);}
static void (*shell_callbacks[])(void)={(void(*)(void))ping};
static void test_global(void *d,struct wl_proxy *r,uint32_t n,const char *name,uint32_t v) {
 (void)d;(void)v;const struct wl_interface *i=NULL;
 if(!strcmp(name,"wl_compositor"))i=dlsym(library,"wl_compositor_interface");
 if(!strcmp(name,"wl_subcompositor"))i=dlsym(library,"wl_subcompositor_interface");
 if(!strcmp(name,"wl_shm"))i=dlsym(library,"wl_shm_interface");
 if(!strcmp(name,"xdg_wm_base"))i=&xdg_wm_base_interface;
 if(!strcmp(name,"wp_viewporter"))i=&wp_viewporter_interface;
 if(!i)return;
 union wl_argument a[4]={{.u=n},{.s=name},{.u=1},{.o=NULL}};
 struct wl_proxy *p=request(r,0,i,a);assert(p);
 if(i==&xdg_wm_base_interface){shell=p;wl_proxy_add_listener(p,shell_callbacks,NULL);}
 if(i==&wp_viewporter_interface)viewporter=p;
}
static void removed(void *d,struct wl_proxy *r,uint32_t n){(void)d;(void)r;(void)n;}
static const struct registry_listener registry_callbacks={test_global,removed};
static void configured_cb(void *d,struct wl_proxy *p,uint32_t serial){(void)d;union wl_argument a={.u=serial};request(p,4,NULL,&a);acks++;}
static void top_cb(void *d,struct wl_proxy *p,int32_t w,int32_t h,struct wl_array *s){(void)d;(void)p;(void)s;assert(w==1280&&h==720);}
static void closed_cb(void *d,struct wl_proxy *p){(void)d;(void)p;}
int main(void) {
 setenv("ANVILDROID_ANDROID_WINDOW","1",1);
 library=dlopen("libwayland-client.so.0",2);assert(library);
 marshal=dlsym(library,"wl_proxy_marshal_array_flags");listen=dlsym(library,"wl_proxy_add_listener");
 get_class=dlsym(library,"wl_proxy_get_class");get_version=dlsym(library,"wl_proxy_get_version");
 struct wl_proxy *(*connect_display)(const char *)=dlsym(library,"wl_display_connect");
 roundtrip=dlsym(library,"wl_display_roundtrip");
 void (*disconnect_display)(struct wl_proxy *)=dlsym(library,"wl_display_disconnect");
 struct wl_proxy *display=connect_display(NULL);assert(display);
 union wl_argument id={.o=NULL};
 struct wl_proxy *registry=request(display,1,dlsym(library,"wl_registry_interface"),&id);
 wl_proxy_add_listener(registry,(void(**)(void))&registry_callbacks,NULL);
 assert(roundtrip(display)>=0 && shell && csd_compositor && csd_shm && csd_subcompositor && viewporter);
 for(int cycle=0;cycle<3;cycle++) {
   struct wl_proxy *s=request(csd_compositor,0,csd_surface_i,&id);
   union wl_argument a[2]={{.o=NULL},{.o=s}};
   struct wl_proxy *vp=request(viewporter,1,&wp_viewport_interface,a);
   struct wl_proxy *x=request(shell,2,&xdg_surface_interface,a);
   void (*xc[])(void)={(void(*)(void))configured_cb};wl_proxy_add_listener(x,xc,NULL);
   struct wl_proxy *t=request(x,1,&xdg_toplevel_interface,&id);
   void (*tc[])(void)={(void(*)(void))top_cb,(void(*)(void))closed_cb};wl_proxy_add_listener(t,tc,NULL);
   union wl_argument title={.s="AnvilDroid Android CSD smoke"};request(t,2,NULL,&title);
   request(s,6,NULL,NULL);
   assert(roundtrip(display)>=0 && acks>cycle);
   /* Same SHM helper as the frame; stand-in for Android's content buffer. */
   struct wl_proxy *buffer=csd_paint(1,1280,720,0);assert(buffer);
   union wl_argument size[2]={{.i=1280},{.i=720}};request(vp,2,NULL,size);
   union wl_argument attach[3]={{.o=buffer},{.i=0},{.i=0}};request(s,1,NULL,attach);
   request(s,6,NULL,NULL);assert(roundtrip(display)>=0);
   if(!manager)assert(csd_pieces[0].surface && csd_visible());
   request(t,9,NULL,NULL);await_state(display,1,0);
   union wl_argument output={.o=NULL};request(t,11,NULL,&output);
   await_state(display,1,1);assert(!csd_visible());
   request(t,12,NULL,NULL);request(t,10,NULL,NULL);
   await_state(display,0,0);
   wl_proxy_marshal_array_flags(t,0,NULL,1,1,NULL);
   wl_proxy_marshal_array_flags(vp,0,NULL,1,1,NULL);
   wl_proxy_marshal_array_flags(x,0,NULL,1,1,NULL);
   wl_proxy_marshal_array_flags(s,0,NULL,1,1,NULL);
   assert(roundtrip(display)>=0 && !top && !csd_pieces[0].surface);
 }
 printf("Android window live: 3 map/close cycles, maximize/fullscreen, %s, no protocol errors\n",manager?"negotiated decoration":"CSD fallback");
 disconnect_display(display);return 0;
}
