/* A fresh KWin profile must receive a CSD request when the bridge owns chrome. */
#include "bridge.c"
#include <assert.h>

int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static struct proxy manager_proxy, decoration_proxy, compositor, subcompositor, shm;
static struct proxy allocated_proxies[32];
static int allocated, requested_mode, frame_requests;
static const struct interface top_interface={.name="xdg_toplevel"};
static const struct message xdg_methods[]={{"ack_configure","u",0}};
static const struct interface xdg_interface={.name="xdg_surface",.methods=xdg_methods};
static struct proxy top_proxy={&top_interface}, xdg_proxy={&xdg_interface};
static uint32_t version(struct proxy *p) {(void)p;return 1;}
static void destroy(struct proxy *p) {(void)p;}
static int listen(struct proxy *p,void (**listener)(void),void *data) {
  (void)p;(void)listener;(void)data;return 0;
}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *args,
                            const struct interface *interface,uint32_t v) {
  (void)interface;(void)v;
  if(p==&manager_proxy) return &decoration_proxy;
  if(p==&decoration_proxy) {if(op==1)requested_mode=args[0].u;return 0;}
  if(p==&top_proxy||p==&xdg_proxy)return 0;
  if(p==&compositor||p==&subcompositor) {
    ++frame_requests;assert(allocated<32);return &allocated_proxies[allocated++];
  }
  return 0;
}
int main(void) {
  real_marshal=record;get_version=version;real_listen=listen;real_destroy=destroy;
  decoration_manager=&manager_proxy;decoration_available=1;
  csd_compositor=&compositor;csd_subcompositor=&subcompositor;csd_shm=&shm;
  struct object surface={.app=1,.app_id="com.android.settings"};
  struct object xdg={.p=&xdg_proxy,.surface=&surface};surface.xdg=&xdg;
  struct object top={.p=&top_proxy,.surface=&surface,.next=&xdg};
  xdg.next=&surface;objects=&top;
  request_window_decoration(&top);
  assert(top.decoration==&decoration_proxy);
  assert(requested_mode==1);
  /* Wait for negotiation; an SSD response must never attach a client frame. */
  csd_create(&top);assert(!top.csd&&!frame_requests);
  union argument serial={.u=7};
  decoration_configure(&top,&decoration_proxy,2);
  wl_proxy_marshal_array_constructor_versioned(&xdg_proxy,0,&serial,0,1);
  assert(top.decoration_mode==2&&!top.csd&&!frame_requests);
#if ANVIL_CSD_EXPERIMENT
  decoration_configure(&top,&decoration_proxy,1);
  wl_proxy_marshal_array_constructor_versioned(&xdg_proxy,0,&serial,0,1);
  assert(top.csd&&surface.csd==top.csd&&frame_requests==8);
  /* Renegotiation back to SSD releases the previously attached bridge frame. */
  decoration_configure(&top,&decoration_proxy,2);
  wl_proxy_marshal_array_constructor_versioned(&xdg_proxy,0,&serial,0,1);
  assert(!top.csd&&!surface.csd);
  decoration_destroy(&top);
  csd_create(&top);assert(top.csd); /* GNOME: no decoration manager. */
  csd_destroy(&top);
#endif
  return 0;
}
