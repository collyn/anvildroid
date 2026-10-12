/* Runs on GTK's thread with its existing Wayland connection. An isolated event
 * queue receives only our registry/token events; unrelated GTK events stay on
 * GTK's queue. Every read is bounded and cancelled on timeout. */
#include <wayland-client.h>
#include "xdg-activation-client.h"
#include <dlfcn.h>
#include <poll.h>
#include <time.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
struct request {
  struct wl_display *display;
  struct wl_event_queue *queue;
  struct xdg_activation_v1 *manager;
  int synced, done;
  char *buffer;
  size_t capacity;
};
static void global(void *data,struct wl_registry *registry,uint32_t name,const char *interface,uint32_t version) {
  (void)version;struct request *r=data;
  if(!strcmp(interface,"xdg_activation_v1")&&!r->manager){
    r->manager=wl_registry_bind(registry,name,&xdg_activation_v1_interface,1);
    wl_proxy_set_queue((struct wl_proxy*)r->manager,r->queue);
  }
}
static void removed(void *data,struct wl_registry *registry,uint32_t name){(void)data;(void)registry;(void)name;}
static const struct wl_registry_listener registry_listener={global,removed};
static void sync_done(void *data,struct wl_callback *callback,uint32_t serial){(void)callback;(void)serial;((struct request*)data)->synced=1;}
static const struct wl_callback_listener sync_listener={sync_done};
static void token_done(void *data,struct xdg_activation_token_v1 *token,const char *text){
  (void)token;struct request *r=data;r->done=1;
  if(!text||!*text||strlen(text)>=r->capacity)return;
  /* The relay is one bounded field in the runtime-local bridge protocol. */
  for(const unsigned char *p=(const unsigned char*)text;*p;++p)
    if(*p<=32||*p>=127)return;
  snprintf(r->buffer,r->capacity,"%s",text);
}
static const struct xdg_activation_token_v1_listener token_listener={token_done};
static long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000+t.tv_nsec/1000000;}
static int wait_for(struct request *r,int *done,long deadline){
  while(!*done){
    if(wl_display_dispatch_queue_pending(r->display,r->queue)<0)return 0;
    if(*done)return 1;
    long remaining=deadline-now_ms();if(remaining<=0)return 0;
    if(wl_display_prepare_read_queue(r->display,r->queue)<0)continue;
    if(wl_display_flush(r->display)<0&&errno!=EAGAIN){wl_display_cancel_read(r->display);return 0;}
    struct pollfd fd={wl_display_get_fd(r->display),POLLIN,0};
    int result=poll(&fd,1,(int)remaining);
    if(result<=0||!(fd.revents&POLLIN)){wl_display_cancel_read(r->display);return 0;}
    if(wl_display_read_events(r->display)<0)return 0;
  }
  return 1;
}
int anvil_activation_token(void *window,char *buffer,size_t capacity){
  if(!window||capacity<2)return 0;
  buffer[0]=0;
  void *(*get_display)(void*)=dlsym(RTLD_DEFAULT,"gdk_window_get_display");
  unsigned long (*display_type)(void)=dlsym(RTLD_DEFAULT,"gdk_wayland_display_get_type");
  int (*is_type)(void*,unsigned long)=dlsym(RTLD_DEFAULT,"g_type_check_instance_is_a");
  struct wl_display *(*get_wl_display)(void*)=dlsym(RTLD_DEFAULT,"gdk_wayland_display_get_wl_display");
  struct wl_surface *(*get_surface)(void*)=dlsym(RTLD_DEFAULT,"gdk_wayland_window_get_wl_surface");
  if(!get_display||!display_type||!is_type||!get_wl_display||!get_surface)return 0;
  void *display=get_display(window);if(!display||!is_type(display,display_type()))return 0;
  struct request r={.display=get_wl_display(display),.buffer=buffer,.capacity=capacity};
  struct wl_surface *surface=get_surface(window);if(!r.display||!surface)return 0;
  r.queue=wl_display_create_queue(r.display);if(!r.queue)return 0;
  struct wl_registry *registry=wl_display_get_registry(r.display);
  wl_proxy_set_queue((struct wl_proxy*)registry,r.queue);
  wl_registry_add_listener(registry,&registry_listener,&r);
  struct wl_callback *sync=wl_display_sync(r.display);
  wl_proxy_set_queue((struct wl_proxy*)sync,r.queue);wl_callback_add_listener(sync,&sync_listener,&r);
  long deadline=now_ms()+250;
  struct xdg_activation_token_v1 *token=0;
  if(wait_for(&r,&r.synced,deadline)&&r.manager){
    token=xdg_activation_v1_get_activation_token(r.manager);
    wl_proxy_set_queue((struct wl_proxy*)token,r.queue);
    xdg_activation_token_v1_add_listener(token,&token_listener,&r);
    xdg_activation_token_v1_set_surface(token,surface);
    /* Source-surface association is standard. Input serials are not exposed by
     * GTK3's public API; compositors requiring one may decline this token. */
    xdg_activation_token_v1_commit(token);wait_for(&r,&r.done,deadline);
  }
  if(token)xdg_activation_token_v1_destroy(token);
  if(r.manager)xdg_activation_v1_destroy(r.manager);
  wl_callback_destroy(sync);wl_registry_destroy(registry);wl_event_queue_destroy(r.queue);
  return buffer[0]!=0;
}
