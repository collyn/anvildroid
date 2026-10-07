/* AnvilDroid: Waydroid 1.6 / Android 13 window geometry adapter.
 * Wayland client ABI + versioned HIDL layer-name adapter; no HWC structure offsets.
 * Loaded only in vendor.hwcomposer-2-1, never in Android apps.
 */
#include "resize-policy.h"
#include "resize-request.h"
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>
#include "game-fit.h"

extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern void *calloc(size_t, size_t);
extern void free(void *);
extern int strcmp(const char *, const char *);
extern int __android_log_print(int, const char *, const char *, ...);
extern void abort(void);
extern int pthread_mutex_lock(void *);
extern int pthread_mutex_unlock(void *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *),
                          void *);
extern int pthread_detach(unsigned long);
extern void *fopen(const char *, const char *);
extern int fclose(void *);
extern int fprintf(void *, const char *, ...);
extern int rename(const char *, const char *);
extern char *fgets(char *, int, void *);
extern int sscanf(const char *, const char *, ...);
extern int snprintf(char *, size_t, const char *, ...);
extern char *strstr(const char *, const char *);
extern char *strchr(const char *, int);
extern char *strrchr(const char *, int);
extern char *strdup(const char *);
extern char *getenv(const char *);
extern int usleep(unsigned int);

/* Waydroid's Android 15 composer configures the HIDL RPC pool twice. The
 * second call can request fewer threads and libhidlbase aborts instead of
 * ignoring the shrink. Keep the first pool size and ignore smaller requests.
 */
static unsigned long anvil_rpc_pool_size;
static int anvil_native_passthrough;
static void anvil_configure_rpc_pool_impl(unsigned long threads, int caller_joins,
                                          const char *symbol) {
  typedef void (*configure_fn)(unsigned long, int);
  static configure_fn real_configure_rpc;
  static configure_fn real_configure_binder;
  configure_fn *slot = strstr(symbol, "configureBinder") != NULL
      ? &real_configure_binder : &real_configure_rpc;
  if (!*slot)
    *slot = (configure_fn)dlsym((void *)-1L, symbol);
  if (!*slot)
    return;
  unsigned long current = __atomic_load_n(&anvil_rpc_pool_size, __ATOMIC_ACQUIRE);
  while (threads > current) {
    if (__atomic_compare_exchange_n(&anvil_rpc_pool_size, &current, threads, 0,
                                    __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
      (*slot)(threads, caller_joins);
      return;
    }
  }
  if (threads == current && current == 0)
    (*slot)(threads, caller_joins);
}

void anvil_configure_rpc_pool(unsigned long threads, int caller_joins)
    __asm__("_ZN7android8hardware22configureRpcThreadpoolEmb");
void anvil_configure_rpc_pool(unsigned long threads, int caller_joins) {
  anvil_configure_rpc_pool_impl(threads, caller_joins,
                                "_ZN7android8hardware22configureRpcThreadpoolEmb");
}

void anvil_configure_binder_rpc_pool(unsigned long threads, int caller_joins)
    __asm__("_ZN7android8hardware29configureBinderRpcThreadpoolEmb");
void anvil_configure_binder_rpc_pool(unsigned long threads, int caller_joins) {
  anvil_configure_rpc_pool_impl(threads, caller_joins,
                                "_ZN7android8hardware29configureBinderRpcThreadpoolEmb");
}

struct message {
  const char *name, *signature;
  const void **types;
};
struct interface {
  const char *name;
  int version, method_count;
  const struct message *methods;
  int event_count;
  const struct message *events;
};
struct proxy {
  const struct interface *interface;
};
union argument {
  int32_t i;
  uint32_t u;
  const char *s;
  void *o;
};
typedef struct proxy *(*marshal_fn)(struct proxy *, uint32_t, union argument *,
                                    const struct interface *, uint32_t);
typedef int (*listen_fn)(struct proxy *, void (**)(void), void *);
static marshal_fn real_marshal;
static struct proxy *(*real_array_flags)(struct proxy *, uint32_t,
    const struct interface *, uint32_t, uint32_t, union argument *);
static listen_fn real_listen;
static void (*real_destroy)(struct proxy *);
static uint32_t (*get_version)(struct proxy *);
static _Alignas(8) unsigned char mutex[64];
#include "display-lifecycle.inc"

struct object {
  struct proxy *p;
  struct wl_display *display;
  struct proxy *pointer_surface;
  int full_ui, full_w, full_h, full_canvas_w, full_canvas_h;
  int fit_w, fit_h, fit_x, fit_y, fit_sw, fit_sh, fit_bar_focus;
  struct game_fit_state *fit;
  int fit_applied, fit_last_x, fit_last_y, fit_last_w, fit_last_h;
  int buffer_transport, transport_reported, transport_seen;
  struct object *next, *parent, *surface, *xdg, *viewport, *region;
  int region_x, region_y, region_width, region_height, region_valid;
  int x, y, width, height, valid, app;
  int bx, by, bw, bh, has_bounds, attached;
  int content_width, content_height, preview_width, preview_height;
  struct resize_policy resize;
  unsigned long profile_token;
  int profile_loaded, profile_dirty, profile_restoring, profile_ticks, profile_recreated;
  int profile_width, profile_height;
  int host_fullscreen;
  char *app_id;
  void (**listener)(void);
  void *listener_data;
  struct proxy *decoration;
  uint32_t decoration_pending, decoration_mode;
  struct csd_frame *csd, *csd_focus;
  struct keymap_overlay *overlay;
  struct keymap_overlay *hints;
  unsigned long hints_probe;
  int hints_task;
  struct object *overlay_focus;
  int overlay_dismissed, overlay_pressed;
  struct object *keyboard_focus;
  uint32_t keymap_blocked_keys[24];
  int keymap_touch_ids[32];
  struct proxy *touch_surfaces[32];
  uint32_t keymap_touch_present,keymap_touch_blocked;
  int csd_part, csd_x, csd_y, csd_pressed, csd_suppress;
};
static struct object *objects;
static int32_t full_ui_input(struct proxy *,int32_t,int);
static int game_fit_enabled(struct object *);
static int game_fit_bar(struct proxy *);
static int32_t game_fit_input(struct proxy *,int32_t,int);
static int game_fit_geometry(struct object *,int,int,int,int);
static void game_fit_destroy(struct object *);
static struct proxy *decoration_manager;
struct decoration_binding {
  struct wl_display *display;
  struct proxy *manager;
  uint32_t name;
  struct decoration_binding *next;
};
static struct decoration_binding *decoration_bindings;
static struct decoration_binding *decoration_for(struct wl_display *display) {
  for (struct decoration_binding *b=decoration_bindings;b;b=b->next)
    if (b->display==display) return b;
  return 0;
}
static uint32_t decoration_global_name;
static int decoration_available;
static const struct interface decoration_interface;
static const void *decoration_types[2] = {&decoration_interface, 0};
static const struct message decoration_manager_methods[] = {
    {"destroy", "", 0}, {"get_toplevel_decoration", "no", decoration_types}};
static const struct interface decoration_manager_interface = {
    "zxdg_decoration_manager_v1", 1, 2, decoration_manager_methods, 0, 0};
static const struct message decoration_methods[] = {
    {"destroy", "", 0}, {"set_mode", "u", 0}, {"unset_mode", "", 0}};
static const struct message decoration_events[] = {{"configure", "u", 0}};
static const struct interface decoration_interface = {
    "zxdg_toplevel_decoration_v1", 1, 3, decoration_methods, 1, decoration_events};
struct task {
  int id, x, y, width, height;
  char package[256];
};
static struct task tasks[128];
static int task_count;
#include "media-layer.inc"
static void *task_worker(void *unused);
static void *ime_worker(void *unused);
static void ime_protocol_init(void *library);
static void init(void) {
  if (real_marshal)
    return;
  /* Custom images use more than one Wayland HWC soname. Prefer the known
   * Waydroid module, then resolve the symbols from the already loaded HWC. */
  void *h = 0;
  const char *libraries[] = {
      "/vendor/lib64/hw/hwcomposer.waydroid.so",
      "/vendor/lib64/hw/hwcomposer.waydroid.so.1",
      "/vendor/lib64/hw/android.hardware.graphics.composer@2.1-impl.waydroid.so",
      0};
  for (int i = 0; libraries[i] && !h; ++i)
    h = dlopen(libraries[i], 2 | 4);
  if (!h)
    h = (void *)-1L;
  real_marshal =
      (marshal_fn)dlsym(h, "wl_proxy_marshal_array_constructor_versioned");
  real_array_flags = (struct proxy *(*)(struct proxy *, uint32_t,
      const struct interface *, uint32_t, uint32_t, union argument *))
      dlsym(h, "wl_proxy_marshal_array_flags");
  real_listen = (listen_fn)dlsym(h, "wl_proxy_add_listener");
  real_destroy = (void (*)(struct proxy *))dlsym(h, "wl_proxy_destroy");
  get_version = (uint32_t (*)(struct proxy *))dlsym(h, "wl_proxy_get_version");
  /* Official images may keep libwayland symbols in the already loaded HWC
   * namespace without exposing them from the vendor HAL soname. */
  if (!real_marshal || !real_listen || !real_destroy || !get_version) {
    void *global = (void *)-1L;
    if (!real_marshal) real_marshal = (marshal_fn)dlsym(global, "wl_proxy_marshal_array_constructor_versioned");
    if (!real_listen) real_listen = (listen_fn)dlsym(global, "wl_proxy_add_listener");
    if (!real_destroy) real_destroy = (void (*)(struct proxy *))dlsym(global, "wl_proxy_destroy");
    if (!get_version) get_version = (uint32_t (*)(struct proxy *))dlsym(global, "wl_proxy_get_version");
    if (!real_array_flags) real_array_flags = (struct proxy *(*)(struct proxy *, uint32_t, const struct interface *, uint32_t, uint32_t, union argument *))dlsym(global, "wl_proxy_marshal_array_flags");
  }
  /* Legacy Waydroid has no marshal_array_flags. Its constructor API is
   * sufficient; do not disable the bridge or skip display connection hooks. */
  display_symbols(h);
  if (!real_marshal || !real_listen || !real_destroy || !get_version) {
    /* An unknown custom HWC must remain usable even when desktop hooks are
     * unavailable. The stock Wayland protocol is enough for boot. */
    anvil_native_passthrough = 1;
    __android_log_print(5, "AnvilDroid",
                        "HWC bridge symbols unavailable; using native passthrough");
    return;
  }
  anvil_native_passthrough = getenv("ANVILDROID_NATIVE_WAYLAND") != NULL;
  __android_log_print(4, "AnvilDroid",
                      "window bridge loaded (Wayland legacy ABI)");
  unsigned long thread;
  if (!pthread_create(&thread, 0, task_worker, 0))
    pthread_detach(thread);
  ime_protocol_init(h);
  if (!pthread_create(&thread, 0, ime_worker, 0))
    pthread_detach(thread);
}
static struct object *find(void *p) {
  for (struct object *o = objects; o; o = o->next)
    if (o->p == p)
      return o;
  return 0;
}
static struct object *track(void *p) {
  if (!p)
    return 0;
  struct object *o = find(p);
  if (o)
    return o;
  o = calloc(1, sizeof(*o));
  if (!o)
    abort();
  o->p = p;
  o->next = objects;
  objects = o;
  return o;
}
static void wayland_send(struct proxy *p, uint32_t op, union argument *a) {
  real_marshal(p, op, a, 0, get_version(p));
}
static void decoration_configure(void *data, struct proxy *p, uint32_t mode) {
  (void)p;
  pthread_mutex_lock(mutex);
  struct object *top = data;
  top->decoration_pending = mode;
  __android_log_print(4, "AnvilDroid", "decoration configure mode=%u", mode);
  pthread_mutex_unlock(mutex);
}
static void (*decoration_listener[])(void) = {(void (*)(void))decoration_configure};
static void request_server_decoration(struct object *top) {
  if (!top) return;
  struct decoration_binding *binding=decoration_for(top->display);
  struct proxy *manager=top->display ? (binding ? binding->manager : 0) : decoration_manager;
  if (!manager || !decoration_available || !top->surface ||
      top->decoration || top->surface->attached)
    return;
  decoration_types[1] = top->p->interface;
  union argument a[2] = {{.o = 0}, {.o = top->p}};
  top->decoration = real_marshal(manager, 1, a,
                                 &decoration_interface, 1);
  if (!top->decoration) return;
  real_listen(top->decoration, decoration_listener, top);
  union argument mode = {.u = 2};
  wayland_send(top->decoration, 1, &mode);
}
static void decoration_destroy(struct object *top) {
  if (!top || !top->decoration) return;
  wayland_send(top->decoration, 0, 0);
  real_destroy(top->decoration);
  top->decoration = 0;
  top->decoration_pending = top->decoration_mode = 0;
}
#include "csd-adapter.inc"
#include "ime-adapter.inc"
#include "scale-adapter.inc"
#include "boot-canvas.inc"
#include "window-profile.inc"
#include "close-adapter.inc"
#include "keymap-overlay.inc"
#include "display-probe.inc"
#include "full-ui.inc"
#include "game-fit.inc"
static void ime_protocol_init(void *library) {
  ime_object_types[0] = &ime_text_interface;
  ime_object_types[1] = dlsym(library, "wl_seat_interface");
  ime_surface_types[0] = dlsym(library, "wl_surface_interface");
}
static void geometry(struct object *s) {
  if (!s || !s->xdg || !s->app)
    return;
  struct object cached = {.x=s->region_x,.y=s->region_y,.width=s->region_width,
                          .height=s->region_height,.valid=s->region_valid};
  struct object *r = s->region ? s->region : &cached;
  if (!r->valid) return;
  struct object exact = *r;
  if (s->app_id) {
    int matches = 0;
    for (int i = 0; i < task_count; i++) {
      if (!strcmp(s->app_id, tasks[i].package)) {
        /* Task bounds are Android coordinates, not Wayland logical units.
         * Match HWC child position/viewport rounding; this does NOT repair
         * SurfaceFlinger clipping when a task exceeds the Android display. */
        exact.x = task_to_logical(tasks[i].x, 0);
        exact.y = task_to_logical(tasks[i].y, 0);
        exact.width = task_to_logical(tasks[i].width, 1);
        exact.height = task_to_logical(tasks[i].height, 1);
        matches++;
      }
    }
    /* Never guess between two independently opened tasks of one package. */
    if (matches == 1)
      r = &exact;
  }
  if (r->width <= 0 || r->height <= 0)
    return;
  s->content_width = r->width;
  s->content_height = r->height;
  if(game_fit_geometry(s,r->x,r->y,r->width,r->height))return;
  /* Commit the requested desktop frame during a drag. Otherwise KWin sees
   * only the old geometry and sends that old size again on button release. */
  struct object preview = *r;
  if (s->preview_width) {
    if (!s->resize.dragging && r->width == s->preview_width &&
        r->height == s->preview_height) {
      s->preview_width = s->preview_height = 0;
    } else {
      preview.width = s->preview_width;
      preview.height = s->preview_height;
      r = &preview;
    }
  }
  if (s->viewport && s->viewport->width > 0) {
    /* HWC parent is a transparent 1x1 background, not the Android content.
     * Its viewport does not scale child surfaces. Preserve canvas coordinates. */
    int w = s->viewport->width, h = s->viewport->height;
    if (r->x + r->width > w) w = r->x + r->width;
    if (r->y + r->height > h) h = r->y + r->height;
    union argument a[2] = {{.i = w}, {.i = h}};
    wayland_send(s->viewport->p, 2, a);
  }
  csd_sync(s, r->x, r->y, r->width, r->height);
  keymap_overlay_sync(s,r->x,r->y,r->width,r->height);
  if (!s->has_bounds || s->bx != r->x || s->by != r->y || s->bw != r->width ||
      s->bh != r->height) {
    s->bx = r->x;
    s->by = r->y;
    s->bw = r->width;
    s->bh = r->height;
    s->has_bounds = 1;
    union argument a[4] = {
        {.i = r->x}, {.i = r->y}, {.i = r->width}, {.i = r->height}};
    if (s->csd && !decoration_available && !s->csd->fullscreen) {
      /* HWC positions content children at displayFrame x/y within this
       * parent. CSD and xdg geometry must use that same canvas origin. */
      a[0].i -= CSD_BORDER; a[1].i -= CSD_HEADER;
      a[2].i += 2 * CSD_BORDER; a[3].i += CSD_HEADER + CSD_BORDER;
    }
    wayland_send(s->xdg->p, 3, a);
    __android_log_print(4, "AnvilDroid", "content geometry %d,%d %dx%d", r->x,
                        r->y, r->width, r->height);
  }
}

/* A desktop configure belongs to one app, never to Android's whole display.
 * Calibration (before a package app_id exists) still uses upstream behavior.
 * Coalesce configure events; older games may recreate their rendering activity
 * for every size. Wait for the interactive drag to end, then three quiet ticks.
 */
static void configure(void *data, struct proxy *p, int32_t w, int32_t h,
                      void *states) {
  struct object *o = data;
  if(o->surface&&o->surface->full_ui) {
    pthread_mutex_lock(mutex);
    full_ui_fit(o->surface,w,h);
    pthread_mutex_unlock(mutex);
    return; /* Never hotplug Android's display for a host-window resize. */
  }
  if (!o->surface || !o->surface->app) {
#ifdef ANVIL_BOOT_CANVAS
    pthread_mutex_lock(mutex);
    if (!boot_decided && w > 1 && h > 1) {
      boot_hint_width = w;
      boot_hint_height = h;
    }
    pthread_mutex_unlock(mutex);
#endif
    ((void (*)(void *, struct proxy *, int32_t, int32_t,
               void *))o->listener[0])(o->listener_data, p, w, h, states);
  } else {
#ifdef ANVIL_DISPLAY_GROWTH_PROBE
    display_probe(o, p, states);
#endif
    pthread_mutex_lock(mutex);
    struct object *s = o->surface;
    struct array {
      size_t size, alloc;
      uint32_t *data;
    } *a = states;
    int resizing = 0, maximized = 0, fullscreen = 0;
    for (size_t i = 0; a && i < a->size / sizeof(uint32_t); ++i) {
      if (a->data[i] == 3)
        resizing = 1;
      if (a->data[i] == 1 || a->data[i] == 2)
        maximized = 1;
      if (a->data[i] == 2) fullscreen = 1;
    }
    if (s->csd) {
      /* Keep the last windowed geometry, not the asynchronously updated
       * Android task size after maximize. Preview is the latest accepted
       * host size when Android is still catching up with a drag. */
      if (maximized && !s->csd->maximized) {
        s->csd->restore_width = game_fit_enabled(s)&&s->fit_w ? s->fit_w :
          (s->preview_width ? s->preview_width : s->content_width);
        s->csd->restore_height = game_fit_enabled(s)&&s->fit_h ? s->fit_h :
          (s->preview_height ? s->preview_height : s->content_height);
      }
      /* Android activities may request immersive/fullscreen for their own
       * content. In desktop mode that must not remove the host window frame:
       * CSD is the window chrome and remains visible for every app, including
       * players and games. Treat the configure as a normal window geometry.
       * The user's F11 host fullscreen (host_fullscreen) is accepted. */
      if (fullscreen && !s->host_fullscreen) {
        __android_log_print(4, "AnvilDroidCsd",
                            "ignore Android fullscreen for desktop CSD app=%s",
                            s->app_id ? s->app_id : "?");
        fullscreen = 0;
      }
      if (s->csd->fullscreen != fullscreen) s->has_bounds = 0;
      if (s->csd->maximized != maximized) s->csd->pieces[0].width = 0;
      s->csd->fullscreen = fullscreen;
      s->csd->maximized = maximized;
      if (!fullscreen && !decoration_available) {
        /* Only the client-drawn frame eats into the configured size; with
         * server-side decorations KWin already configures the content area. */
        if (w > 0) w -= 2 * CSD_BORDER;
        if (h > 0) h -= CSD_HEADER + CSD_BORDER;
      }
      /* Zero is unspecified, not a request for a zero-sized window. Keep
       * explicit compositor dimensions authoritative (e.g. new workarea).
       * Also handle repeated unspecified configures after leaving maximize. */
      if (!maximized) {
        if (w == 0) w = s->csd->restore_width;
        if (h == 0) h = s->csd->restore_height;
        if (w >= 64 && h >= 64 && w <= 8192 && h <= 8192) {
          s->csd->restore_width = w;
          s->csd->restore_height = h;
        }
      }
    }
    if (s->has_bounds || (s->csd && s->content_width)) {
      __android_log_print(4, "AnvilDroid",
                          "configure %s %dx%d resizing=%d maximized=%d current=%dx%d pending=%dx%d",
                          s->app_id ? s->app_id : "?", w, h, resizing, maximized,
                          s->bw, s->bh, s->resize.width, s->resize.height);
      if(game_fit_enabled(s)) {
        if(w>=64&&h>=64&&w<=8192&&h<=8192){s->fit_w=w;s->fit_h=h;}
        s->resize.width=s->resize.height=0;
        s->resize.dragging=resizing;s->resize.maximized=maximized;
        s->resize.ready=1;
        pthread_mutex_unlock(mutex);
        return; /* Host presentation only; never relayout the Android game. */
      }
      resize_configure(&s->resize, w, h, s->content_width, s->content_height,
                       resizing, maximized);
      /* An explicit user drag wins over a delayed initial profile read. */
      if (resizing) {
        s->profile_loaded = 1;
        s->profile_restoring = 0;
        s->profile_recreated = 0;
      }
      if (s->profile_loaded && !s->profile_restoring && !s->profile_recreated &&
          !maximized && s->resize.width && s->resize.ready) {
        s->profile_dirty = 1;
        s->profile_ticks = 0;
      }
      if (w >= 64 && h >= 64 && w <= 8192 && h <= 8192 && s->resize.ready) {
        s->preview_width = w;
        s->preview_height = h;
      }
    }
    pthread_mutex_unlock(mutex);
  }
}
static void close_window(void *data, struct proxy *p) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  int handled=close_request_app(o->surface);
  pthread_mutex_unlock(mutex);
  if(handled)return;
  ((void (*)(void *, struct proxy *))o->listener[1])(o->listener_data, p);
}
static void (*toplevel_listener[])(void) = {(void (*)(void))configure,
                                            (void (*)(void))close_window};

int wl_proxy_add_listener(struct proxy *p, void (**listener)(void),
                          void *data) {
  pthread_mutex_lock(mutex);
  init();
  if (anvil_native_passthrough) {
    int result = real_listen(p, listener, data);
    pthread_mutex_unlock(mutex);
    return result;
  }
  struct object *o = track(p);
  o->listener = listener;
  o->listener_data = data;
  int result;
  if (!strcmp(p->interface->name, "xdg_toplevel"))
    result = real_listen(p, toplevel_listener, o);
  else if (!strcmp(p->interface->name, "wl_registry"))
    result = real_listen(p, ime_registry_listener, o);
  else if (!strcmp(p->interface->name, "wl_output") && get_version(p) <= 3)
    result = real_listen(p, scale_output_listener, o);
  else if (!strcmp(p->interface->name, "wp_fractional_scale_v1") && get_version(p) == 1)
    result = real_listen(p, scale_fractional_listener, o);
  else if (ANVIL_CSD_EXPERIMENT && !strcmp(p->interface->name, "wl_pointer") && get_version(p) <= 5)
    result = real_listen(p, csd_pointer_listener, o);
  else if (ANVIL_KEYMAP_EDITOR && !strcmp(p->interface->name,"wl_keyboard") && get_version(p)<=5)
    result = real_listen(p,keymap_keyboard_listener,o);
  else if (ANVIL_KEYMAP_EDITOR && !strcmp(p->interface->name,"wl_touch") && get_version(p)<=5)
    result = real_listen(p,keymap_touch_listener,o);
  else
    result = real_listen(p, listener, data);
  pthread_mutex_unlock(mutex);
  return result;
}

static void forget_proxy(struct proxy *p);
static struct proxy *bridge_marshal(
    struct proxy *p, uint32_t op, union argument *a,
    const struct interface *interface, uint32_t version, uint32_t flags,
    int modern) {
  pthread_mutex_lock(mutex);
  init();
  if (anvil_native_passthrough) {
    struct proxy *result = modern
        ? real_array_flags(p, op, interface, version, flags, a)
        : real_marshal(p, op, a, interface, version);
    pthread_mutex_unlock(mutex);
    return result;
  }
  const char *cls = p->interface->name;
  const char *method = p->interface->methods[op].name;
  struct object *o = find(p);
  int skip = 0;
  if ((!strcmp(cls, "xdg_toplevel") || !strcmp(cls, "wl_surface")) && !strcmp(method, "destroy"))
    csd_destroy(o);
  if (!strcmp(cls,"wl_surface") && !strcmp(method,"destroy")) {
    game_fit_destroy(o);
    keymap_overlay_destroy(o); keymap_hints_destroy(o);
  }
  if (!strcmp(cls, "xdg_toplevel") && !strcmp(method, "destroy"))
    decoration_destroy(o);
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "ack_configure") && o) {
    for (struct object *top = objects; top; top = top->next)
      if (top->decoration && top->surface == o->surface)
        top->decoration_mode = top->decoration_pending;
  }
  char runtime_identity[1024];
  const char *original_identity = 0;
  const char *runtime_id = getenv("ANVILDROID_RUNTIME_ID");
  int scoped_runtime = runtime_id && runtime_id[0] == 'r' && runtime_id[1] == '-';
  if (scoped_runtime) {
    int i;
    for (i = 2; runtime_id[i] && i < 34; ++i)
      if (!((runtime_id[i] >= 'a' && runtime_id[i] <= 'f') ||
            (runtime_id[i] >= '0' && runtime_id[i] <= '9'))) break;
    scoped_runtime = i == 34 && runtime_id[i] == 0;
  }
  if (!strcmp(cls, "wp_viewport") && !strcmp(method, "set_destination") && o) {
    o->width = a[0].i;
    o->height = a[1].i;
  }
  if (o && !strcmp(cls,"wl_subsurface") && !strcmp(method,"set_position")) {
    o->x = a[0].i; o->y = a[1].i;
  }
  if (!strcmp(cls, "xdg_toplevel") && !strcmp(method, "set_app_id")) {
    if (o && o->surface) {
      const char *id = a[0].s;
      const char *incoming=id;
      if(incoming&&strstr(incoming,"waydroid.")==incoming)incoming+=9;
      if(o->surface->app_id&&(!incoming||strcmp(o->surface->app_id,incoming))) {
        keymap_overlay_destroy(o->surface);keymap_hints_destroy(o->surface);
      }
      /* Waydroid prefixes host app_ids. Exclude the calibration/full UI
       * surface. */
      o->surface->app =
          id && strcmp(id, "Waydroid") && strcmp(id, "waydroid") &&
          strcmp(id, "waydroid.Waydroid") && strcmp(id, "waydroid.InputMethod");
      free(o->surface->app_id);
      const char *package = id;
      if (id && strstr(id, "waydroid.") == id)
        package = id + 9;
      o->surface->app_id = package ? strdup(package) : 0;
      if(!package||strcmp(package,"Waydroid"))o->surface->full_ui=0;
#ifdef ANVIL_BOOT_CANVAS
      if(!o->surface->app&&package&&!strcmp(package,"Waydroid")&&boot_decided&&boot_width>0&&boot_height>0&&scale_has_viewporter) {
        struct object *s=o->surface;s->full_ui=1;
        s->full_canvas_w=boot_width;s->full_canvas_h=boot_height;
        full_ui_fit(s,1000,700);
        wayland_send(o->p,10,0); /* Initial full UI must not auto-maximize. */
      }
#endif
      csd_create(o);
    }
  }
  /* CSD buttons use real_marshal directly. Suppress HWC's automatic maximize
   * policy, which otherwise opens every Android app maximized on GNOME. */
  if (o && o->csd && !strcmp(cls,"xdg_toplevel") && !strcmp(method,"set_maximized"))
    skip = 1;
  /* Android immersive activities must not turn the desktop window into a
   * borderless fullscreen surface. Keep the CSD frame authoritative. */
  if (o && o->csd && !strcmp(cls, "xdg_toplevel") &&
      !strcmp(method, "set_fullscreen")) {
    wayland_send(o->p, 12, 0); /* unset_fullscreen */
    skip = 1;
  }
  /* Keep Android package IDs above for task/IME routing; scope only host identity. */
  if (scoped_runtime && !strcmp(cls, "xdg_toplevel") &&
      (!strcmp(method, "set_app_id") || !strcmp(method, "set_title")) && a && a[0].s) {
    if (!strcmp(method, "set_app_id")) {
      snprintf(runtime_identity, sizeof(runtime_identity), "waydroid.anvildroid.%s.%s", runtime_id, strstr(a[0].s, "waydroid.") == a[0].s ? a[0].s + 9 : a[0].s);
      original_identity = a[0].s;
      a[0].s = runtime_identity;
    } else if (!strcmp(method, "set_title")) {
      snprintf(runtime_identity, sizeof(runtime_identity), "[%s] %s", runtime_id, a[0].s);
      original_identity = a[0].s;
      a[0].s = runtime_identity;
    }
  }
  if (!strcmp(cls, "wl_region") && o) {
    if (!strcmp(method, "subtract"))
      o->valid = 0;
    if (!strcmp(method, "add")) {
      /* Waydroid expands the layer's input region by 15 logical pixels for
       * Android resize grips. */
      int x = a[0].i + 15, y = a[1].i + 15, w = a[2].i - 30, h = a[3].i - 30;
      if (w > 0 && h > 0) {
        if (!o->valid) {
          o->x = x;
          o->y = y;
          o->width = w;
          o->height = h;
          o->valid = 1;
        } else {
          int right = o->x + o->width, bottom = o->y + o->height;
          if (x + w > right)
            right = x + w;
          if (y + h > bottom)
            bottom = y + h;
          if (x < o->x)
            o->x = x;
          if (y < o->y)
            o->y = y;
          o->width = right - o->x;
          o->height = bottom - o->y;
        }
      }
    }
  }
  if (!strcmp(cls, "wl_surface") && o) {
    if (!strcmp(method, "attach")) {
      o->attached = a[0].o != 0;
      struct object *buffer=find(a[0].o);
      o->buffer_transport=buffer?buffer->buffer_transport:0;
    }
    if (!strcmp(method, "set_input_region")) {
      o->region = find(a[0].o);
      /* wl_surface copies the region at this request. Modern HWC destroys
       * its temporary wl_region before commit; geometry must retain bounds. */
      o->region_valid=o->region && o->region->valid;
      if (o->region_valid) {
        o->region_x=o->region->x;o->region_y=o->region->y;
        o->region_width=o->region->width;o->region_height=o->region->height;
      }
    }
    if (!strcmp(method, "commit")) {
      game_fit_report_transport(o);
      ime_sync();
      geometry(o);
      /* Do not map the temporary display-sized transparent background.
       * The empty initial commit still negotiates xdg configure normally. */
      if (o->app && o->xdg && o->attached && !o->has_bounds)
        skip = 1;
    }
  }
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "set_window_geometry") &&
      o && o->surface && o->surface->has_bounds)
    skip = 1;
#ifdef ANVIL_DISPLAY_GROWTH_PROBE
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "set_window_geometry") &&
      o && o->surface && o->surface == display_probe_surface)
    skip = 1;
#endif
  struct proxy *temporary_region=0;
  full_ui_request(o,cls,method,a,&temporary_region);
  game_fit_request(o,cls,method,a,&temporary_region);
  struct proxy *result = skip ? 0 : modern
      ? real_array_flags(p, op, interface, version, flags, a)
      : real_marshal(p, op, a, interface, version);
  if(temporary_region){wayland_send(temporary_region,0,0);real_destroy(temporary_region);}
  if (original_identity) a[0].s = original_identity;
  if(o&&o->surface&&o->surface->full_ui&&!strcmp(cls,"xdg_surface")&&!strcmp(method,"ack_configure"))
    full_ui_apply(o->surface);
  /* Respond on the Wayland event thread, after acknowledging the configure.
   * This is a state-only commit; app buffers and Android tasks are untouched. */
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "ack_configure") &&
      o && o->surface && o->surface->app &&
      (o->surface->has_bounds || (o->surface->csd && o->surface->content_width))) {
    geometry(o->surface);
    wayland_send(o->surface->p, 6, 0);
  }
  if (result && interface) {
    if (!strcmp(interface->name, "wp_viewporter")) scale_has_viewporter = 1;
    struct object *n = track(result);
    n->display = o ? o->display : 0;
    if(!strcmp(interface->name,"wl_buffer")) {
      if(!strcmp(cls,"wl_shm_pool"))n->buffer_transport=1;
      else if(!strcmp(cls,"zwp_linux_buffer_params_v1"))n->buffer_transport=2;
      else if(!strcmp(cls,"wl_drm")||!strcmp(cls,"android_wlegl"))n->buffer_transport=3;
    }
    csd_track_global(result, interface);
    if (!strcmp(interface->name,"wl_subsurface")) {
      n->surface = track(a[1].o);
      n->parent = track(a[2].o);
    }
    if (!strcmp(interface->name, "wl_pointer")) n->parent = o;
    if (!strcmp(interface->name, "wl_seat") && !ime_seat) {
      ime_seat = result;
      ime_create();
    }
    if (!strcmp(interface->name, "xdg_surface")) {
      n->surface = track(a[1].o);
      n->surface->xdg = n;
    }
    if (!strcmp(interface->name, "xdg_toplevel"))
      n->surface = o ? o->surface : 0;
    if (!strcmp(interface->name, "wp_viewport")) {
      n->surface = track(a[1].o);
      if (n->surface)
        n->surface->viewport = n;
    }
    if (!strcmp(interface->name, "xdg_toplevel"))
      request_server_decoration(n);
  }
  if (modern && (flags & 1)) forget_proxy(p);
  pthread_mutex_unlock(mutex);
  return result;
}

struct proxy *wl_proxy_marshal_array_constructor_versioned(
    struct proxy *p, uint32_t op, union argument *a,
    const struct interface *interface, uint32_t version) {
  return bridge_marshal(p, op, a, interface, version, 0, 0);
}

/* New wayland-scanner stubs use marshal_flags instead of the legacy entry
 * point. Share tracking/geometry, but preserve libwayland's atomic destroy. */
struct proxy *wl_proxy_marshal_flags(struct proxy *p, uint32_t op,
    const struct interface *interface, uint32_t version, uint32_t flags, ...) {
  union argument args[20];
  const char *signature = p->interface->methods[op].signature;
  va_list ap;
  va_start(ap, flags);
  unsigned count = 0;
  for (const char *s = signature; s && *s; ++s) {
    if ((*s >= '0' && *s <= '9') || *s == '?') continue;
    if (count == 20) abort();
    switch (*s) {
    case 'i': case 'f': case 'h': args[count].i = va_arg(ap, int32_t); break;
    case 'u': args[count].u = va_arg(ap, uint32_t); break;
    case 's': args[count].s = va_arg(ap, const char *); break;
    case 'o': case 'n': case 'a': args[count].o = va_arg(ap, void *); break;
    default: abort();
    }
    ++count;
  }
  va_end(ap);
  return bridge_marshal(p, op, args, interface, version, flags, 1);
}

static void forget_proxy(struct proxy *p) {
  struct object **at = &objects;
  while (*at && (*at)->p != p)
    at = &(*at)->next;
  if (*at) {
    struct object *old = *at;
    keymap_overlay_destroy(old);
    keymap_hints_destroy(old);
    game_fit_destroy(old);
    csd_destroy(old);
    if (p == csd_compositor) csd_compositor = 0;
    if (p == csd_subcompositor) csd_subcompositor = 0;
    if (p == csd_shm) csd_shm = 0;
    decoration_destroy(old);
    if (old->p == decoration_manager)
      decoration_manager = 0;
    if (ime_surface == old->p) {
      ime_surface = 0;
      ime_sync();
    }
    *at = old->next;
    for (struct object *o = objects; o; o = o->next) {
      if(o->pointer_surface==old->p)o->pointer_surface=0;
      for(int i=0;i<32;++i)if(o->touch_surfaces[i]==old->p)o->touch_surfaces[i]=0;
      if (o->surface == old)
        o->surface = 0;
      if(o->keyboard_focus==old)o->keyboard_focus=0;
      if (o->parent == old) o->parent = 0;
      if (o->xdg == old)
        o->xdg = 0;
      if (o->region == old)
        o->region = 0;
      if (o->viewport == old)
        o->viewport = 0;
    }
    free(old->app_id);
    free(old);
  }
}

void wl_proxy_destroy(struct proxy *p) {
  pthread_mutex_lock(mutex);
  init();
  if (anvil_native_passthrough) {
    real_destroy(p);
    pthread_mutex_unlock(mutex);
    return;
  }
  forget_proxy(p);
  real_destroy(p);
  pthread_mutex_unlock(mutex);
}

static void bridge_connected(struct wl_display *display) {
  track(display)->display=display;
}
static void bridge_wrapper_created(void *wrapper,void *proxy) {
  struct object *parent=find(proxy);
  track(wrapper)->display=parent?parent->display:0;
}
static void bridge_wrapper_removed(void *wrapper) {
  forget_proxy(wrapper);
}
static void bridge_disconnecting(struct wl_display *display) {
  /* libwayland destroys every queue at disconnect. Drop bindings and object
   * metadata while proxies still belong to a live display, never on reuse. */
  __android_log_print(4,"AnvilDroid","display disconnect %p",display);
  for (;;) {
    struct object *o=objects;
    while (o && o->display!=display) o=o->next;
    if (!o) break;
    forget_proxy(o->p);
  }
  struct decoration_binding **at=&decoration_bindings;
  while (*at) {
    struct decoration_binding *b=*at;
    if (b->display!=display) {at=&b->next;continue;}
    wayland_send(b->manager,0,0);real_destroy(b->manager);
    *at=b->next;free(b);
  }
  decoration_manager=decoration_bindings?decoration_bindings->manager:0;
  decoration_global_name=decoration_bindings?decoration_bindings->name:0;
  decoration_available=decoration_manager!=0;
  if (ime_display==display) {
    if (ime_text) {wayland_send(ime_text,0,0);real_destroy(ime_text);}
    if (ime_manager) {wayland_send(ime_manager,0,0);real_destroy(ime_manager);}
    ime_manager=ime_text=ime_seat=ime_surface=0;ime_display=0;
    ime_enabled=ime_serial=ime_manager_name=0;
    ime_state=(struct anvil_ime_state){0};
  }
}

/* The shell-UID companion supplies authoritative task bounds. It never calls
 * Wayland; the renderer thread consumes the snapshot on its next commit.
 */
static void *task_worker(void *unused) {
  (void)unused;
  int logged_scale = 0;
  for (;;) {
    struct task snapshot[128];
    int count = 0;
    char line[4096];
    void *f = fopen("/data/waydroid_tmp/anvildroid/tasks", "r");
    if (f) {
      while (fgets(line, sizeof(line), f)) {
        char *t = strstr(line, "taskId="), *b = strstr(line, "bounds=[");
        if (!t || !b || !strstr(line, "visible=true") || count == 128)
          continue;
        struct task task = {0};
        char component[512];
        int right, bottom;
        if (sscanf(t, "taskId=%d: %511s", &task.id, component) != 2 ||
            sscanf(b, "bounds=[%d,%d][%d,%d]", &task.x, &task.y, &right,
                   &bottom) != 4)
          continue;
        char *slash = strchr(component, '/');
        if (!slash || (size_t)(slash - component) >= sizeof(task.package))
          continue;
        *slash = 0;
        snprintf(task.package, sizeof(task.package), "%.255s", component);
        task.width = right - task.x;
        task.height = bottom - task.y;
        if (task.width > 0 && task.height > 0)
          snapshot[count++] = task;
      }
      fclose(f);
    }
    pthread_mutex_lock(mutex);
    for (int i = 0; i < count; i++) {
      int changed = logged_scale != hwc_scale_120 || i >= task_count ||
          tasks[i].id != snapshot[i].id || tasks[i].x != snapshot[i].x ||
          tasks[i].y != snapshot[i].y || tasks[i].width != snapshot[i].width ||
          tasks[i].height != snapshot[i].height ||
          strcmp(tasks[i].package, snapshot[i].package);
      tasks[i] = snapshot[i];
      if (changed)
        __android_log_print(4, "AnvilDroid",
                            "task %d %s android=(%d,%d %dx%d) logical=%dx%d scale=%d/120",
                            tasks[i].id, tasks[i].package, tasks[i].x, tasks[i].y, tasks[i].width,
                            tasks[i].height, task_to_logical(tasks[i].width, 1),
                            task_to_logical(tasks[i].height, 1), hwc_scale_120);
    }
    task_count = count;
    logged_scale = hwc_scale_120;
    pthread_mutex_unlock(mutex);
    profile_poll();
    close_poll();
    keymap_io_poll();
    int id = -1, width = 0, height = 0;
    pthread_mutex_lock(mutex);
    for (struct object *s = objects; s; s = s->next) {
      if (!s->app || !s->app_id || game_fit_enabled(s))
        continue;
      if (!resize_due(&s->resize) || id >= 0)
        continue;
      int matches = 0;
      for (int i = 0; i < count; i++) {
        if (!strcmp(s->app_id, tasks[i].package)) {
          id = tasks[i].id;
          matches++;
        }
      }
      if (matches == 1) {
        width = logical_to_task(s->resize.width);
        height = logical_to_task(s->resize.height);
        profile_note_resize(s->app_id, id);
      } else {
        id = -1;
      }
      if (matches == 1) resize_submitted(&s->resize);
      else s->resize.width = s->resize.height = 0;
    }
    pthread_mutex_unlock(mutex);
    if (id >= 0 && width >= 64 && height >= 64 && width <= 8192 &&
        height <= 8192) {
      void *request = fopen("/data/waydroid_tmp/anvildroid/resize.tmp", "w");
      if (request) {
        char line[64];
        format_resize_request(line, sizeof(line), id, width, height);
        fprintf(request, "%s", line);
        fclose(request);
        rename("/data/waydroid_tmp/anvildroid/resize.tmp",
               "/data/waydroid_tmp/anvildroid/resize");
        __android_log_print(4, "AnvilDroid",
                            "resize task %d physical=%dx%d scale=%d/120 after quiet interval",
                            id, width, height, hwc_scale_120);
      }
    }
    usleep(RESIZE_POLL_US);
  }
  return 0;
}
