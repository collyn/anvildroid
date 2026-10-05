/* A wl_display proxy wrapper is NOT a struct wl_display. Capture connections
 * only at public connect APIs; own a wrapper solely for a private sync queue.
 * The queue gate pins connection/queue lifetime while dispatching callbacks.
 * Lock order: queue gate -> bridge mutex. keymap_wake holds only bridge mutex.
 */
struct wl_display;
struct wl_event_queue;
static struct wl_display *(*real_display_connect)(const char *);
static struct wl_display *(*real_display_connect_fd)(int);
static void (*real_display_disconnect)(struct wl_display *);
static int (*real_display_flush)(struct wl_display *);
static struct wl_event_queue *(*real_create_queue)(struct wl_display *);
static void (*real_queue_destroy)(struct wl_event_queue *);
static void *(*real_create_wrapper)(void *);
static void (*real_wrapper_destroy)(void *);
static void (*real_set_queue)(struct proxy *, struct wl_event_queue *);
static int (*real_dispatch)(struct wl_display *);
static int (*real_dispatch_pending)(struct wl_display *);
static int (*real_dispatch_queue)(struct wl_display *, struct wl_event_queue *);
static int (*real_dispatch_queue_pending)(struct wl_display *,
                                          struct wl_event_queue *);
static struct wl_display *keymap_display;
static struct wl_event_queue *keymap_event_queue;
static struct proxy *keymap_sync_proxy, *keymap_callback;
static uint64_t keymap_display_epoch;
static int keymap_scheduled, keymap_display_blocked;
static _Alignas(8) unsigned char keymap_queue_gate[64];
static _Thread_local unsigned keymap_dispatch_depth;
static void init(void);
static void keymap_connection_lost(void);
static void bridge_connected(struct wl_display *);
static void bridge_disconnecting(struct wl_display *);
static void bridge_wrapper_created(void *, void *);
static void bridge_wrapper_removed(void *);

static void display_symbols(void *h) {
  real_display_connect =
      (struct wl_display * (*)(const char *)) dlsym(h, "wl_display_connect");
  real_display_connect_fd =
      (struct wl_display * (*)(int)) dlsym(h, "wl_display_connect_to_fd");
  real_display_disconnect =
      (void (*)(struct wl_display *))dlsym(h, "wl_display_disconnect");
  real_display_flush =
      (int (*)(struct wl_display *))dlsym(h, "wl_display_flush");
  real_create_queue = (struct wl_event_queue * (*)(struct wl_display *))
      dlsym(h, "wl_display_create_queue");
  real_queue_destroy =
      (void (*)(struct wl_event_queue *))dlsym(h, "wl_event_queue_destroy");
  real_create_wrapper = (void *(*)(void *))dlsym(h, "wl_proxy_create_wrapper");
  real_wrapper_destroy = (void (*)(void *))dlsym(h, "wl_proxy_wrapper_destroy");
  real_set_queue = (void (*)(struct proxy *, struct wl_event_queue *))dlsym(
      h, "wl_proxy_set_queue");
  real_dispatch = (int (*)(struct wl_display *))dlsym(h, "wl_display_dispatch");
  real_dispatch_pending =
      (int (*)(struct wl_display *))dlsym(h, "wl_display_dispatch_pending");
  real_dispatch_queue =
      (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(
          h, "wl_display_dispatch_queue");
  real_dispatch_queue_pending =
      (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(
          h, "wl_display_dispatch_queue_pending");
}
static int keymap_connection_ready(void) {
  return keymap_display && keymap_sync_proxy && keymap_event_queue &&
         !keymap_display_blocked;
}
static void keymap_cancel_callback(void) {
  if (keymap_callback) {
    real_destroy(keymap_callback);
    keymap_callback = 0;
  }
  keymap_scheduled = 0;
}
static void display_connected(struct wl_display *display) {
  if (!display)
    return;
  pthread_mutex_lock(keymap_queue_gate);
  pthread_mutex_lock(mutex);
  bridge_connected(display);
  if (keymap_display && keymap_display != display) {
    keymap_cancel_callback();
    keymap_connection_lost();
    keymap_display_blocked = 1;
    __android_log_print(5, "AnvilKeymapOverlay",
                        "multiple display connections: editor disabled until "
                        "compositor restart");
  } else if (!keymap_display && !keymap_display_blocked && real_create_queue &&
             real_queue_destroy && real_create_wrapper &&
             real_wrapper_destroy && real_set_queue &&
             real_dispatch_queue_pending && real_display_flush) {
    keymap_display = display;
    ++keymap_display_epoch;
    keymap_event_queue = real_create_queue(display);
    if (keymap_event_queue)
      keymap_sync_proxy = real_create_wrapper(display);
    if (keymap_sync_proxy)
      real_set_queue(keymap_sync_proxy, keymap_event_queue);
    else {
      if (keymap_event_queue)
        real_queue_destroy(keymap_event_queue);
      keymap_event_queue = 0;
      keymap_display_blocked = 1;
    }
    __android_log_print(4, "AnvilKeymapOverlay",
                        "display connection captured; private queue ready=%d",
                        keymap_connection_ready());
  }
  pthread_mutex_unlock(mutex);
  pthread_mutex_unlock(keymap_queue_gate);
}
struct wl_display *wl_display_connect(const char *name) {
  pthread_mutex_lock(mutex);
  init();
  pthread_mutex_unlock(mutex);
  struct wl_display *display =
      real_display_connect ? real_display_connect(name) : 0;
  display_connected(display);
  return display;
}
struct wl_display *wl_display_connect_to_fd(int fd) {
  pthread_mutex_lock(mutex);
  init();
  pthread_mutex_unlock(mutex);
  struct wl_display *display =
      real_display_connect_fd ? real_display_connect_fd(fd) : 0;
  display_connected(display);
  return display;
}
void wl_display_disconnect(struct wl_display *display) {
  pthread_mutex_lock(keymap_queue_gate);
  pthread_mutex_lock(mutex);
  init();
  if (display == keymap_display) {
    keymap_cancel_callback();
    keymap_connection_lost();
    if (keymap_sync_proxy)
      real_wrapper_destroy(keymap_sync_proxy);
    if (keymap_event_queue)
      real_queue_destroy(keymap_event_queue);
    keymap_sync_proxy = 0;
    keymap_event_queue = 0;
    keymap_display = 0;
    ++keymap_display_epoch;
    // Generic bridge proxies are not a reconnectable object graph. Fail closed
    // for keymaps after disconnect instead of binding old surfaces to a new fd.
    keymap_display_blocked = 1;
  }
  bridge_disconnecting(display);
  pthread_mutex_unlock(mutex);
  if (real_display_disconnect)
    real_display_disconnect(display);
  pthread_mutex_unlock(keymap_queue_gate);
}
void *wl_proxy_create_wrapper(void *proxy) {
  pthread_mutex_lock(mutex);
  init();
  void *wrapper = real_create_wrapper ? real_create_wrapper(proxy) : 0;
  if (wrapper) bridge_wrapper_created(wrapper, proxy);
  pthread_mutex_unlock(mutex);
  return wrapper;
}
void wl_proxy_wrapper_destroy(void *wrapper) {
  pthread_mutex_lock(mutex);
  init();
  bridge_wrapper_removed(wrapper);
  if (real_wrapper_destroy) real_wrapper_destroy(wrapper);
  pthread_mutex_unlock(mutex);
}
static void keymap_dispatch_queued(struct wl_display *display) {
  pthread_mutex_lock(keymap_queue_gate);
  pthread_mutex_lock(mutex);
  struct wl_event_queue *queue =
      display == keymap_display && keymap_connection_ready()
          ? keymap_event_queue
          : 0;
  pthread_mutex_unlock(mutex);
  if (queue) {
    ++keymap_dispatch_depth;
    int result = real_dispatch_queue_pending(display, queue);
    --keymap_dispatch_depth;
    if (result < 0) {
      pthread_mutex_lock(mutex);
      keymap_cancel_callback();
      keymap_connection_lost();
      keymap_display_blocked = 1;
      pthread_mutex_unlock(mutex);
    }
  }
  pthread_mutex_unlock(keymap_queue_gate);
}
int wl_display_dispatch(struct wl_display *display) {
  if (!real_dispatch) {
    pthread_mutex_lock(mutex);
    init();
    pthread_mutex_unlock(mutex);
  }
  ++keymap_dispatch_depth;
  int result = real_dispatch ? real_dispatch(display) : -1;
  --keymap_dispatch_depth;
  if (!keymap_dispatch_depth && result >= 0)
    keymap_dispatch_queued(display);
  return result;
}
int wl_display_dispatch_pending(struct wl_display *display) {
  if (!real_dispatch_pending) {
    pthread_mutex_lock(mutex);
    init();
    pthread_mutex_unlock(mutex);
  }
  ++keymap_dispatch_depth;
  int result = real_dispatch_pending ? real_dispatch_pending(display) : -1;
  --keymap_dispatch_depth;
  if (!keymap_dispatch_depth && result >= 0)
    keymap_dispatch_queued(display);
  return result;
}
int wl_display_dispatch_queue(struct wl_display *display,
                              struct wl_event_queue *queue) {
  if (!real_dispatch_queue) {
    pthread_mutex_lock(mutex);
    init();
    pthread_mutex_unlock(mutex);
  }
  ++keymap_dispatch_depth;
  int result = real_dispatch_queue ? real_dispatch_queue(display, queue) : -1;
  --keymap_dispatch_depth;
  if (!keymap_dispatch_depth && result >= 0)
    keymap_dispatch_queued(display);
  return result;
}
int wl_display_dispatch_queue_pending(struct wl_display *display,
                                      struct wl_event_queue *queue) {
  if (!real_dispatch_queue_pending) {
    pthread_mutex_lock(mutex);
    init();
    pthread_mutex_unlock(mutex);
  }
  ++keymap_dispatch_depth;
  int result = real_dispatch_queue_pending
                   ? real_dispatch_queue_pending(display, queue)
                   : -1;
  --keymap_dispatch_depth;
  if (!keymap_dispatch_depth && result >= 0)
    keymap_dispatch_queued(display);
  return result;
}
