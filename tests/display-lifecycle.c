#define ANVIL_KEYMAP_EDITOR 1
#include "../native/bridge/bridge.c"
#include <assert.h>

struct wl_display { struct proxy proxy; unsigned magic; };
struct wl_event_queue { unsigned magic; };
static const struct interface callback_i={.name="wl_callback"};
static const void *sync_types[]={&callback_i};
static const struct message display_methods[]={{"sync","n",sync_types}};
static const struct interface display_i={.name="wl_display",.methods=display_methods};
static struct wl_display display={{&display_i},123},other={{&display_i},456};
static struct wl_event_queue queue={789};
static struct proxy owned_wrapper={&display_i},callback={&callback_i};
static int wrappers,queues,flushes,callbacks,drains,pending,disconnected,fail_connect;
static int destroyed_callback,destroyed_wrapper,destroyed_queue;
static void (**callback_listener)(void);
static void *callback_data;
int __android_log_print(int l,const char *t,const char *f,...){(void)l;(void)t;(void)f;return 0;}
static struct wl_display *connect_fd(int fd){assert(fd==9);return &display;}
static struct wl_display *connect_name(const char *name){(void)name;return fail_connect?0:wl_display_connect_to_fd(9);}
static struct wl_event_queue *create_queue(struct wl_display *d){assert(d==&display);++queues;return &queue;}
static void *create_wrapper(void *d){assert(d==&display);++wrappers;return &owned_wrapper;}
static void set_queue(struct proxy *p,struct wl_event_queue *q){assert(p==&owned_wrapper&&q==&queue);}
static void destroy_wrapper(void *p){assert(p==&owned_wrapper);assert(!pending);++destroyed_wrapper;}
static void destroy_queue(struct wl_event_queue *q){assert(q==&queue);assert(destroyed_wrapper);++destroyed_queue;}
static void disconnect_display(struct wl_display *d){assert(d==&display||d==&other);if(d==&display)assert(destroyed_queue);++disconnected;}
static int flush_display(struct wl_display *d){assert(d==&display&&d->magic==123);++flushes;return 0;}
static struct proxy *marshal(struct proxy *p,uint32_t op,union argument *args,const struct interface *iface,uint32_t version){
  (void)args;(void)version;
  if(p==&owned_wrapper){assert(op==0&&iface==&callback_i);++callbacks;return &callback;}
  // Foreign EGL wrapper carries the wl_display interface but is NOT a display.
  assert(p->interface==&display_i);return 0;
}
static int listen(struct proxy *p,void (**listener)(void),void *data){assert(p==&callback);callback_listener=listener;callback_data=data;pending=1;return 0;}
static void destroy_proxy(struct proxy *p){assert(p==&callback);pending=0;++destroyed_callback;}
static int queue_pending(struct wl_display *d,struct wl_event_queue *q){
  assert(d==&display);
  if(q==&queue){++drains;if(pending){pending=0;((void(*)(void*,struct proxy*,uint32_t))callback_listener[0])(callback_data,&callback,42);}}
  return 0;
}
static int dispatch_pending(struct wl_display *d){assert(d==&display);return 0;}
static int dispatch_display(struct wl_display *d){
  // Mirror libwayland's nested public dispatch calls: only the outer wrapper
  // drains our queue; never recurse/deadlock while inside private dispatch.
  assert(wl_display_dispatch_pending(d)==0);return 7;
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static void setup(void){
  real_marshal=marshal;real_listen=listen;real_destroy=destroy_proxy;get_version=version;
  real_display_connect=connect_name;real_display_connect_fd=connect_fd;real_display_disconnect=disconnect_display;
  real_display_flush=flush_display;real_create_queue=create_queue;real_queue_destroy=destroy_queue;
  real_create_wrapper=create_wrapper;real_wrapper_destroy=destroy_wrapper;real_set_queue=set_queue;
  real_dispatch=dispatch_display;real_dispatch_pending=dispatch_pending;
  real_dispatch_queue_pending=queue_pending;
}
int main(void){
  setup();
  assert(wl_display_connect(0)==&display);
  assert(queues==1&&wrappers==1&&keymap_display_epoch==1&&keymap_connection_ready());
  struct proxy *foreign=calloc(1,sizeof(*foreign));assert(foreign);foreign->interface=&display_i;
  union argument a={.o=0};
  wl_proxy_marshal_array_constructor_versioned(foreign,0,&a,0,1);
  assert(keymap_display==&display&&keymap_sync_proxy==&owned_wrapper);
  free(foreign); // Regression: stale foreign wrappers must never be retained.
  keymap_wake();assert(flushes==1&&callbacks==1&&pending);
  keymap_wake();assert(flushes==1); // One outstanding callback.
  assert(wl_display_dispatch(&display)==7);
  assert(drains==1&&!pending&&!keymap_scheduled&&destroyed_callback==1);
  assert(flushes==2); // Callback repaint is flushed without another app frame.
  fail_connect=1;assert(!wl_display_connect(0));assert(keymap_connection_ready());fail_connect=0;
  keymap_wake();void *old_epoch=callback_data;assert(pending);
  wl_display_disconnect(&display);
  assert(!keymap_display&&!keymap_sync_proxy&&!keymap_event_queue&&!pending&&!keymap_scheduled);
  int prior=destroyed_callback;
  keymap_done(old_epoch,&callback,43); // Late cancelled callback is harmless.
  keymap_wake();assert(flushes==3&&destroyed_callback==prior);
  assert(wl_display_connect(0)==&display);assert(!keymap_connection_ready()); // Fail closed after teardown.

  // Simulate a fresh process for ambiguity guard.
  keymap_display_blocked=0;keymap_display_epoch=0;destroyed_queue=destroyed_wrapper=0;
  display_connected(&display);assert(keymap_connection_ready());
  display_connected(&other);assert(keymap_display_blocked&&!keymap_connection_ready());
  keymap_wake();assert(flushes==3);
  wl_display_disconnect(&other);wl_display_disconnect(&display);
  assert(disconnected==3);
  return 0;
}
