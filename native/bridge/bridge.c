/* AnvilDroid: Waydroid 1.6 / Android 13 window geometry adapter.
 * Wayland client ABI + versioned HIDL layer-name adapter; no HWC structure offsets.
 * Loaded only in vendor.hwcomposer-2-1, never in Android apps.
 */
#include "resize-policy.h"
#include "custom-size.h"
#include "resize-request.h"
#include "open-size-policy.h"
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
extern int __system_property_get(const char *, char *) __attribute__((weak));
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
static int trace_frames;
#define FRAME_TRACE(...) do { if (trace_frames) __android_log_print(__VA_ARGS__); } while (0)

/* Interpose the HIDL ProcessState entry point used by setupBinderPolling.
 * Leave both configure*RpcThreadpool wrappers alone: one calls the other,
 * and suppressing that nested call skips actual pool initialization. */
#include "hidl-pool.inc"
static int anvil_native_passthrough;
/* This socket is configured only by runtime-nvidia.py. Detect the selected
 * guest backend, never merely the presence of an NVIDIA GPU on the host. */
static int resize_nvidia_backend = -1;
static int resize_uses_nvidia(void) {
  int enabled = __atomic_load_n(&resize_nvidia_backend, __ATOMIC_ACQUIRE);
  if (enabled >= 0) return enabled;
  int (*get)(const char *, char *) = dlsym((void *)0, "__system_property_get");
  char socket[92] = "";
  enabled = get && get("mesa.vtest.socket.name", socket) > 0 &&
    !strcmp(socket, "/dev/venus/venus.sock");
  __atomic_store_n(&resize_nvidia_backend, enabled, __ATOMIC_RELEASE);
  return enabled;
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
  struct nv_ring *nv_ring, *nv_pending;
  int nv_plane_fd, nv_nvidia, nv_planes;
  uint32_t nv_stride, nv_offset, nv_formats[32];
  unsigned nv_format_count;
  uint64_t nv_modifier;
  struct proxy *p;
  const char *protocol_name; /* Immutable protocol metadata, not proxy internals. */
  const struct interface *protocol_interface;
  struct wl_display *display;
  struct proxy *pointer_surface;
  int absolute_x, absolute_y, absolute_valid, absolute_buttons;
  uint32_t focus_blocked_button;
  struct custom_size custom_size;
  int full_ui, full_w, full_h, full_canvas_w, full_canvas_h;
  int full_clip_x,full_clip_y;
  int boot_geometry_w, boot_geometry_h;
  int window_configured, window_content, window_activated;
  int fit_w, fit_h, fit_x, fit_y, fit_sw, fit_sh, fit_bar_focus;
  int fit_delivered_w, fit_delivered_h;
  struct game_fit_state *fit;
  int fit_applied, fit_last_x, fit_last_y, fit_last_w, fit_last_h;
  int buffer_transport, transport_reported, transport_seen;
  struct object *next, *parent, *surface, *xdg, *viewport, *region;
  int region_x, region_y, region_width, region_height, region_valid;
  int frame_origin_valid, frame_origin_x, frame_origin_y;
  int x, y, width, height, valid, app;
  int source_x, source_y, source_width, source_height;
  struct proxy *edit_cursor;
  uint32_t cursor_serial;
  struct proxy *cursor_surface;
  int cursor_hotspot_x,cursor_hotspot_y,cursor_requested,cursor_fallback;
  int committed_attached; /* Buffer state after the last forwarded surface commit. */
  struct proxy *attached_buffer;
  int attach_x, attach_y, clip_hidden, clip_x, clip_y;
  int bx, by, bw, bh, has_bounds, attached;
  uint32_t buffer_stride;
  int content_width, content_height, preview_width, preview_height;
  struct resize_policy resize;
  unsigned long profile_token;
  int profile_loaded, profile_dirty, profile_restoring, profile_ticks, profile_recreated;
  int initial_presented, initial_wait_ticks;
  int profile_width, profile_height;
  int profile_state_dirty;
  int host_fullscreen;
  char *app_id;
  void (**listener)(void);
  void *listener_data;
  struct proxy *decoration;
  uint32_t decoration_pending, decoration_mode;
  struct csd_frame *csd, *csd_focus, *csd_resize, *menu_focus;
  struct keymap_overlay *overlay;
  struct keymap_overlay *hints;
  unsigned long hints_probe;
  int hints_task;
  int keymap_disabled, keymap_hidden;
  struct object *overlay_focus;
  int overlay_dismissed, overlay_pressed;
  struct object *keyboard_focus;
  int keymap_pointer_leave_pending;
  struct proxy *keymap_pointer_leave_surface;
  int keyboard_leave_pending;
  struct proxy *keyboard_leave_surface;
  uint32_t keymap_blocked_keys[24];
  int keymap_touch_ids[32];
  struct proxy *touch_surfaces[32];
  uint32_t keymap_touch_present,keymap_touch_blocked;
  int csd_part, csd_x, csd_y, csd_pressed, csd_suppress;
};
static struct object *objects;
static int32_t full_ui_input(struct proxy *,int32_t,int);
static int32_t content_clip_input(struct proxy *,int32_t,int);
static int content_clip_layer(struct object *,int,int,int,int);
static void geometry(struct object *);
static int game_fit_enabled(struct object *);
static int window_fit_enabled(struct object *);
static int window_fit_settled(struct object *);
static int window_layout_width(struct object *,int);
static int game_fit_bar(struct proxy *);
static struct object *game_fit_owner(struct object *);
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
struct keymap_overlay;
static int scrcpy_mapping_key(struct object *, struct keymap_overlay *, int, int);
static void scrcpy_mapping_cancel(void);
static void scrcpy_mapping_tick(void);
static void scrcpy_mapping_release_key(int);
static int scrcpy_mapping_escape(void);
static void scrcpy_mapping_relative(struct object *,int32_t,int32_t);
static int keymap_capture_start(struct object *);
static int keymap_capture_active(struct object *);
static int keymap_capture_hides_cursor(struct object *);
static int keymap_capture_supported(struct object *);
static int __attribute__((unused)) keymap_capture_status(struct object *);
static void keymap_capture_stop(void);
static void keymap_capture_protocol_init(void *);
static int keymap_capture_global(struct object *,uint32_t,const char *,uint32_t);
static int keymap_capture_remove(struct object *,uint32_t);
static void keymap_capture_disconnect(struct wl_display *);
static void keymap_capture_forget(struct object *);
static void keymap_capture_cursor_request(struct object *,union argument *);
static struct object *keymap_owner(struct proxy *);
static struct task tasks[128];
static int task_count;
#include "media-layer.inc"
#include "content-frame.inc"
static int object_is(const struct object *, const char *);
static void *task_worker(void *unused);
static void *ime_worker(void *unused);
static void clipboard_input(uint32_t);
static void ime_protocol_init(void *library);
static void window_activation_protocol_init(void *library);
static void window_activation_global(struct object *,uint32_t,uint32_t);
static int window_activation_remove(struct object *,uint32_t);
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
  trace_frames = getenv("ANVILDROID_TRACE_FRAMES") != NULL;
  __android_log_print(4, "AnvilDroid",
                      "window bridge loaded (Wayland legacy ABI)");
  unsigned long thread;
  if (!pthread_create(&thread, 0, task_worker, 0))
    pthread_detach(thread);
  ime_protocol_init(h);
  window_activation_protocol_init(h);
  keymap_capture_protocol_init(h);
  if (!pthread_create(&thread, 0, ime_worker, 0))
    pthread_detach(thread);
}
static int object_is(const struct object *o, const char *name) {
  return o && o->p && o->protocol_name && !strcmp(o->protocol_name, name);
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
static void nv_commit(struct object *);
static int nv_internal_attach(struct object *,struct proxy *);
static void wayland_send(struct proxy *p, uint32_t op, union argument *a) {
  if(op==1&&p&&p->interface&&!strcmp(p->interface->name,"wl_surface")&&a&&nv_internal_attach(find(p),a[0].o))return;
  if(op==6&&p&&p->interface&&!strcmp(p->interface->name,"wl_surface"))nv_commit(find(p));
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
static void request_window_decoration(struct object *top) {
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
  /* The desktop bridge draws its own frame. Asking KWin for server-side
   * decoration as well creates two headers on a fresh desktop profile. */
  union argument mode = {.u = 1};
  wayland_send(top->decoration, 1, &mode);
  __android_log_print(4, "AnvilDroid", "decoration request mode=%u", mode.u);
}
static void decoration_destroy(struct object *top) {
  if (!top || !top->decoration) return;
  wayland_send(top->decoration, 0, 0);
  real_destroy(top->decoration);
  top->decoration = 0;
  top->decoration_pending = top->decoration_mode = 0;
}
#include "csd-adapter.inc"
#include "csd-cursor.inc"
#include "nvidia-present.inc"
#include "ime-adapter.inc"
#include "scale-adapter.inc"
#include "boot-canvas.inc"
#include "window-profile.inc"
#include "resize-mode.inc"
#include "task-watch.h"
#include "window-activation.inc"
#include "window-feedback.inc"
#include "window-transition.inc"
#include "close-adapter.inc"
#include "keymap-overlay.inc"
#include "scrcpy-mapping.inc"
#include "keymap-capture.inc"
#include "display-probe.inc"
#include "display-size.inc"
#include "full-ui.inc"
#include "game-fit.inc"
#include "content-clip.inc"
#include "csd-menu.inc"
static void ime_cursor_geometry(struct object *s,int *x,int *y,int *h){
  if(s->full_ui){
    int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
    *x=ox+full_ui_scaled(s,*x-display_projection.x,0,0);
    *y=oy+full_ui_scaled(s,*y-display_projection.y,1,0);
    *h=full_ui_scaled(s,*h,1,1);
  }else if(window_fit_enabled(s)) {
    struct game_fit_rect r=game_fit_area(s);
    *x=r.x+game_fit_scaled(s,task_to_logical(*x-s->frame_origin_x,0)-s->fit_x,0);
    *y=r.y+game_fit_scaled(s,task_to_logical(*y-s->frame_origin_y,0)-s->fit_y,1);
    *h=game_fit_scaled(s,task_to_logical(*h,1),1);
    if(*h<1)*h=1;
  }else{*x-=s->bx;*y-=s->by;}
}
static void ime_protocol_init(void *library) {
  clip_manager_i=dlsym(library,"wl_data_device_manager_interface");
  clip_source_i=dlsym(library,"wl_data_source_interface");
  clip_device_i=dlsym(library,"wl_data_device_interface");
  if(!clip_manager_i)clip_manager_i=&clip_manager_interface;
  if(!clip_source_i)clip_source_i=&clip_source_interface;
  if(!clip_device_i)clip_device_i=&clip_device_interface;
  ime_object_types[0] = &ime_text_interface;
  ime_object_types[1] = dlsym(library, "wl_seat_interface");
  ime_surface_types[0] = dlsym(library, "wl_surface_interface");
}
static void geometry(struct object *s) {
  if (!s || !s->xdg || (!s->app && !s->full_ui))
    return;
  if (s->full_ui) {
    /* The canvas presentation owns its geometry, viewport and input; the
     * CSD frame merely overlays the fitted extents inside the surface. */
    keymap_canvas_bounds(s);
    keymap_overlay_sync(s,s->bx,s->by,s->bw,s->bh);
    csd_sync(s, 0, 0, s->full_w > 0 ? s->full_w : 1,
             s->full_h > 0 ? s->full_h : 1);
    return;
  }
  struct object cached = {.x=s->region_x,.y=s->region_y,.width=s->region_width,
                          .height=s->region_height,.valid=s->region_valid};
  struct object exact = {0};
  int task_matches = 0;
  if (s->app_id) {
    for (int i = 0; i < task_count; i++) {
      if (!strcmp(s->app_id, tasks[i].package)) {
        /* Task bounds are Android coordinates, not Wayland logical units.
         * NVIDIA subtracts its content-frame origin before placing children;
         * subtract the same physical origin before scale rounding here.
         * Match HWC child position/viewport rounding; this does NOT repair
         * SurfaceFlinger clipping when a task exceeds the Android display. */
        exact.x = task_to_logical(tasks[i].x - s->frame_origin_x, 0);
        exact.y = task_to_logical(tasks[i].y - s->frame_origin_y, 0);
        exact.width = task_to_logical(tasks[i].width, 1);
        exact.height = task_to_logical(tasks[i].height, 1);
        task_matches++;
      }
    }
  }
  struct object *r = s->region ? s->region : &cached;
  if (!r->valid && !s->frame_origin_valid) {
    /* The NVIDIA Venus HWC never sets a surface input region, so the
     * region-derived frame is unavailable. Task positions are canvas
     * coordinates, not surface-local: derive the content rect from the
     * app's own subsurfaces instead. */
    if (task_matches != 1) {
      FRAME_TRACE(4, "AnvilDroid",
                          "geom: region invalid app_id=%s tasks=%d bounds=%d attached=%d",
                          s->app_id ? s->app_id : "?", task_matches,
                          s->has_bounds, s->attached);
      return;
    }
    int found = 0;
    for (struct object *sub = objects; sub; sub = sub->next) {
      if (sub->parent != s || !sub->surface ||
          strcmp(sub->p->interface->name, "wl_subsurface")) continue;
      int sw = sub->surface->width, sh = sub->surface->height;
      if (sw < 1 || sh < 1) continue;
      int sx = sub->x, sy = sub->y;
      __android_log_print(4, "AnvilDroid", "csd layer %d,%d %dx%d",
                          sx, sy, sw, sh);
      if (!found) {
        exact.x = sx; exact.y = sy;
        exact.width = sx + sw; exact.height = sy + sh;
        found = 1;
      } else {
        if (sx < exact.x) exact.x = sx;
        if (sy < exact.y) exact.y = sy;
        if (sx + sw > exact.width) exact.width = sx + sw;
        if (sy + sh > exact.height) exact.height = sy + sh;
      }
    }
    if (found) {
      /* Anchor the frame to the surface origin: the transparent caption
       * strip above the first layer must not leave a gap below the CSD
       * header; the clip repositions the layers flush underneath it. */
      exact.x = exact.y = 0;
      /* The layers' extent is the app content (task minus its insets);
       * resizing the task to that would shrink it in a loop. The task
       * bounds stay the authoritative resize target. */
      for (int i = 0; i < task_count; i++)
        if (!strcmp(s->app_id, tasks[i].package)) {
          exact.width = task_to_logical(tasks[i].width, 1);
          exact.height = task_to_logical(tasks[i].height, 1);
          break;
        }
    } else {
      /* No layers yet: fall back to the task size at the surface origin;
       * the header still lands on the window. */
      exact.x = exact.y = 0;
    }
    r = &exact;
  } else if (task_matches == 1) {
    /* A valid HWC region is in the same surface coordinates as the task
     * translated through the observed content-frame origin (zero on stock),
     * but may begin in the surface's shadow padding (e.g. y=20 for a
     * task starting at y=32). Use the complete task rectangle: combining
     * the padded origin with the task height chops off the bottom inset. */
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
    /* The worker aligns Android's width, and fractional scaling may round
     * either extent up. Retire the preview when that actual task arrives;
     * keeping the unsnapped frame would leave content outside the CSD. */
    int settled = r->width == s->preview_width && r->height == s->preview_height;
    if (task_matches == 1 &&
        r->width == task_to_logical(resize_task_width(logical_to_task(s->preview_width), resize_uses_nvidia()), 1) &&
        r->height == task_to_logical(logical_to_task(s->preview_height), 1))
      settled = 1;
    if (!s->resize.dragging && settled) {
      s->preview_width = s->preview_height = 0;
    } else {
      preview.width = s->preview_width;
      preview.height = s->preview_height;
      r = &preview;
    }
  }
  if (s->viewport && s->viewport->width > 0) {
    /* The parent is HWC's background, not the Android content; changing
     * its viewport does not scale children. NVIDIA also uses an opaque
     * padded background, so shrink as well as grow to the content extent.
     * xdg geometry/input regions alone do not clip the parent's pixels. */
    int w = r->x + r->width, h = r->y + r->height;
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    union argument a[2] = {{.i = w}, {.i = h}};
    wayland_send(s->viewport->p, 2, a);
  }
  content_clip_sync(s, r->x, r->y, r->width, r->height);
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
    if (s->csd && s->csd->maximized && s->csd->max_width > 0 &&
        !s->csd->fullscreen) {
      /* The maximized geometry is the workarea exactly; a pitch-snapped
       * content must never push it past and break the maximized state. */
      a[0].i = r->x; a[1].i = r->y;
      if (s->csd->pieces[0].surface) {
        a[0].i -= CSD_BORDER; a[1].i -= CSD_HEADER;
      }
      a[2].i = s->csd->max_width; a[3].i = s->csd->max_height;
    } else if (s->csd && s->csd->pieces[0].surface && !s->csd->fullscreen) {
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
  if (o->surface) {
    struct window_states { size_t size, alloc; uint32_t *data; } *a=states;
    int activated=0;
    for (size_t i=0;a&&i<a->size/sizeof(uint32_t);++i) if(a->data[i]==4)activated=1;
    pthread_mutex_lock(mutex);o->surface->window_activated=activated;pthread_mutex_unlock(mutex);
  }
  if(o->surface&&o->surface->full_ui) {
    pthread_mutex_lock(mutex);
    struct array { size_t size, alloc; uint32_t *data; } *a = states;
    int maximized = 0, fullscreen = 0, resizing = 0;
    for (size_t i = 0; a && i < a->size / sizeof(uint32_t); ++i) {
      if (a->data[i] == 1 || a->data[i] == 2) maximized = 1;
      if (a->data[i] == 2) fullscreen = 1;
      if (a->data[i] == 3) resizing = 1;
    }
    struct csd_frame *f = o->surface->csd;
    if (f) {
      int changed = f->maximized != maximized || f->fullscreen != fullscreen;
      f->maximized = maximized;
      f->fullscreen = fullscreen;
      /* The canvas HWC commits sparsely; repaint the button glyph (restore
       * vs maximize) immediately instead of waiting for pointer motion. */
      if (changed) csd_repaint_header(f);
    }
    __android_log_print(4, "AnvilDroidCsd", "fullui configure %dx%d max=%d full=%d",
                        w, h, maximized, fullscreen);
    if (full_ui_chrome(o->surface)) {
      if (w>0) w=w>2*CSD_BORDER?w-2*CSD_BORDER:1;
      if (h>0) h=h>CSD_HEADER+CSD_BORDER?h-CSD_HEADER-CSD_BORDER:1;
    }
    if(o->surface->custom_size.enabled) {
      if(w>0&&h>0){o->surface->full_w=w;o->surface->full_h=h;}
      custom_size_queue(&o->surface->custom_size,w,h,resizing);
    } else full_ui_fit(o->surface, w, h, maximized);
    pthread_mutex_unlock(mutex);
    return; /* Never hotplug Android's display for a host-window resize. */
  }
  if (!o->surface || !o->surface->app) {
#ifdef ANVIL_BOOT_CANVAS
    pthread_mutex_lock(mutex);
    if (o->surface) {
      struct boot_states { size_t size, alloc; uint32_t *data; } *a=states;
      int constrained=0;
      for(size_t i=0;a&&i<a->size/sizeof(uint32_t);++i)
        if(a->data[i]==1||a->data[i]==2)constrained=1;
      o->surface->boot_geometry_w=constrained&&w>0?w:0;
      o->surface->boot_geometry_h=constrained&&h>0?h:0;
    }
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
      if (a->data[i] == 3) resizing = 1;
    }
    /* The initial content-only geometry predates our CSD frame. Wait for the
     * durable profile before letting normal configures relayout Android;
     * otherwise that relayout can recreate a game before restoration runs. */
    if (s->app && !game_fit_enabled(s) && !s->profile_loaded && !resizing && !maximized &&
        !profile_recreated(s->app_id)) {
      pthread_mutex_unlock(mutex);
      return;
    }
    if (s->csd) {
      /* A Size-menu selection restores a maximized window to the requested
       * content dimensions, rather than the compositor's previous normal size. */
      if(s->csd->size_pending) {
        if(maximized&&!resizing) {pthread_mutex_unlock(mutex);return;}
        if(!resizing) {
          w=s->csd->restore_width;h=s->csd->restore_height;
          if(s->csd->pieces[0].surface){w+=2*CSD_BORDER;h+=CSD_HEADER+CSD_BORDER;}
        }
        s->csd->size_pending=0;
      }
      /* A recreated toplevel can have a content-only configure queued before
       * its CSD geometry is committed. It must not eat the header twice. */
      if (s->csd->normal_pending && !resizing && !maximized &&
          w==s->csd->restore_width && h==s->csd->restore_height) {
        w+=2*CSD_BORDER;h+=CSD_HEADER+CSD_BORDER;
      } else if (w>0 && h>0) s->csd->normal_pending=0;
      /* A new toplevel may already have a normal configure queued before
       * the restored maximize request. Ack it, but do not overwrite the
       * remembered state or enqueue a normal Android relayout. */
      if (s->csd->maximize_pending && !maximized) {
        s->csd->maximize_retry=1;
        __android_log_print(4,"AnvilDroidCsd",
                            "defer stale normal configure %s %dx%d while restoring maximize",
                            s->app_id,w,h);
        pthread_mutex_unlock(mutex);
        return;
      }
      /* Keep the last windowed geometry, not the asynchronously updated
       * Android task size after maximize. Preview is the latest accepted
       * host size when Android is still catching up with a drag. */
      if (maximized && !s->csd->maximized) {
        s->csd->restore_width = window_fit_enabled(s)&&s->fit_w ? s->fit_w :
          (s->preview_width ? s->preview_width : s->content_width);
        s->csd->restore_height = window_fit_enabled(s)&&s->fit_h ? s->fit_h :
          (s->preview_height ? s->preview_height : s->content_height);
      }
      /* A recreated maximized toplevel has no normal geometry in KWin yet.
       * Its first restore configure can still be the large Android surface. */
      if (!maximized && s->csd->maximized && !resizing &&
          s->csd->restore_width>=64 && s->csd->restore_height>=64) {
        w=s->csd->restore_width;h=s->csd->restore_height;
        if (s->csd->pieces[0].surface) {w+=2*CSD_BORDER;h+=CSD_HEADER+CSD_BORDER;}
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
      if (s->csd->maximized != maximized) s->profile_state_dirty = 1;
      if (s->csd->maximized != maximized) s->csd->pieces[0].width = 0;
      s->csd->fullscreen = fullscreen;
      s->csd->maximized = maximized;
      if (maximized && w > 0 && h > 0) {
        s->csd->max_width = w;
        s->csd->max_height = h;
        /* Publish the workarea geometry right away: an idle app may not
         * commit for seconds, and KWin withdraws the maximize when the
         * window geometry still reflects the pre-maximize size. */
        /* Repeated configures must preserve the same surface-local origin
         * as geometry(). Resetting it to zero puts the negative-y CSD header
         * above KWin's workarea and leaves an equal gap at the bottom. */
        int gx=s->has_bounds?s->bx:0,gy=s->has_bounds?s->by:0;
        if (!fullscreen && s->csd->pieces[0].surface) {
          gx-=CSD_BORDER;gy-=CSD_HEADER;
        }
        union argument g[4] = {{.i=gx},{.i=gy},{.i=w},{.i=h}};
        if(s->xdg) wayland_send(s->xdg->p, 3, g);
      }
      if (!fullscreen && s->csd->pieces[0].surface) {
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
      /* The HWC maps the app window at the full display before the task
       * bounds settle. Resizing the task to that transient configure blows
       * the app up to near-fullscreen; ignore it. */
      int display_sized = 0;
      if (!resizing && !maximized) {
        for (int i = 0; i < task_count; i++)
          if (!strcmp(tasks[i].package, "com.android.launcher3")) {
            if (w + 64 >= tasks[i].width && h + 64 >= tasks[i].height)
              display_sized = 1;
            break;
          }
      }
      if (!display_sized) {
        int fitted = window_fit_enabled(s) && s->fit;
        if(fitted&&!maximized&&!fullscreen&&w>=64&&h>=64&&w<=8192&&h<=8192) {
          w=window_layout_width(s,w);
          if(s->csd){s->csd->restore_width=w;s->csd->restore_height=h;}
        }
        if (fitted && w >= 64 && h >= 64 && w <= 8192 && h <= 8192) {
          s->fit_w = w; s->fit_h = h;
        }
        resize_configure(&s->resize, w, h,
                         fitted && s->fit_delivered_w ? s->fit_delivered_w : s->content_width,
                         fitted && s->fit_delivered_h ? s->fit_delivered_h : s->content_height,
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
          /* Legacy compositors without a fit viewport retain their pending
           * frame. Cancelled drags must clear that frame immediately. */
          if (!fitted && s->resize.width) {
            s->preview_width = w;
            s->preview_height = h;
          } else {
            s->preview_width = s->preview_height = 0;
          }
        }
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
  o->protocol_name = p->interface->name;
  o->protocol_interface = p->interface;
  o->listener = listener;
  o->listener_data = data;
  int result;
  if(o->nv_ring) result=0; /* Ring already owns the physical release listeners. */
  else if(nv_copy_enabled()&&!strcmp(p->interface->name,"zwp_linux_dmabuf_v1"))
    result=real_listen(p,nv_dmabuf_listener,o);
  else if (!strcmp(p->interface->name, "xdg_toplevel"))
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
  if (o) {o->protocol_name = cls;o->protocol_interface = p->interface;}
  if(!strcmp(cls,"wl_pointer")&&!strcmp(method,"set_cursor")) {
    keymap_capture_cursor_request(o,a);
    csd_cursor_request(o,a);
  }
  int skip = 0;
  struct proxy *nv_result=0;
  int nv_constructor=0;
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
      if (top->decoration && top->surface == o->surface) {
        top->decoration_mode = top->decoration_pending;
        /* Apply decoration negotiation with its matching configure. A
         * compositor can decline CSD; never draw a second frame over SSD. */
        if (top->decoration_mode == 2)
          csd_destroy(top);
        else if (top->decoration_mode == 1)
          csd_create(top);
      }
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
  if (!strcmp(cls, "wp_viewport") && !strcmp(method, "set_source") && o) {
    o->source_x = a[0].i; o->source_y = a[1].i;
    o->source_width = a[2].i; o->source_height = a[3].i;
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
       * surface and the IME, but the Waydroid drawer app keeps its CSD. */
      o->surface->app =
          id && strcmp(id, "Waydroid") && strcmp(id, "waydroid") &&
          strcmp(id, "waydroid.InputMethod");
      if (!o->surface->app)
        __android_log_print(4, "AnvilDroid", "csd excluded app_id=%s",
                            id ? id : "?");
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
        full_ui_fit(s,1000,700,0);
        wayland_send(o->p,10,0); /* Initial full UI must not auto-maximize. */
      }
#endif
      csd_create(o);
    }
  }
  /* CSD buttons use real_marshal directly. Suppress HWC's automatic maximize
   * policy, which otherwise opens every Android app maximized on GNOME. */
  if (o && o->csd && !strcmp(cls,"xdg_toplevel") &&
      (!strcmp(method,"set_maximized") || !strcmp(method,"unset_maximized")))
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
      struct proxy *previous_buffer=o->attached_buffer;
      o->attached = a[0].o != 0;
      o->attached_buffer = a[0].o;
      o->attach_x = a[1].i; o->attach_y = a[2].i;
      o->clip_hidden = 0;
      struct object *buffer=find(a[0].o);
      o->buffer_transport=buffer?buffer->buffer_transport:0;
      o->buffer_stride=buffer?buffer->buffer_stride:0;
      o->nv_pending=buffer?buffer->nv_ring:0;
      if(o->nv_pending) {
        /* Keep a real copied destination for geometry-only restores and for
         * skipped/failed copies; the logical proxy has never been written. */
        o->attached_buffer=previous_buffer;skip=1;
      }
    }
    if (!strcmp(method, "set_input_region")) {
      o->region = find(a[0].o);
      /* wl_surface copies the region at this request. Modern HWC destroys
       * its temporary wl_region before commit; geometry must retain bounds. */
      o->region_valid=o->region && o->region->valid;
      if (o->app)
        FRAME_TRACE(4, "AnvilDroid",
                            "input_region app_id=%s region=%p valid=%d",
                            o->app_id ? o->app_id : "?", (void *)o->region,
                            o->region_valid);
      if (o->region_valid) {
        o->region_x=o->region->x;o->region_y=o->region->y;
        o->region_width=o->region->width;o->region_height=o->region->height;
      }
    }
    if (!strcmp(method, "commit")) {
      game_fit_report_transport(o);
      ime_sync();
      geometry(o);
      content_clip_commit(o);
      /* Do not map the temporary display-sized transparent background.
       * The empty initial commit still negotiates xdg configure normally. */
      if (o->app && o->xdg && o->attached &&
          (!o->has_bounds || initial_layout_pending(o))) {
        FRAME_TRACE(4, "AnvilDroid",
                            "commit SKIP app_id=%s bounds=%d region_valid=%d",
                            o->app_id ? o->app_id : "?", o->has_bounds,
                            o->region_valid);
        skip = 1;
      }
      /* Hybrid NVIDIA: never forward a buffer whose pitch is not
       * 256-byte-aligned — radeonsi rejects the import and kills the whole
       * Wayland display on the first failure. Drop the frame; the next
       * (snap-resized) buffer passes. */
      if (resize_uses_nvidia() && o->app && o->xdg && o->attached && o->buffer_stride &&
          (o->buffer_stride & 255) != 0) {
        __android_log_print(4, "AnvilDroid",
                            "commit DROP unaligned stride=%u app_id=%s",
                            o->buffer_stride, o->app_id ? o->app_id : "?");
        skip = 1;
      }
    }
  }
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "set_window_geometry") &&
      o && o->surface && o->surface->has_bounds)
    skip = 1;
#ifdef ANVIL_BOOT_CANVAS
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "set_window_geometry"))
    boot_canvas_geometry(o, a);
#endif
#ifdef ANVIL_DISPLAY_GROWTH_PROBE
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "set_window_geometry") &&
      o && o->surface && o->surface == display_probe_surface)
    skip = 1;
#endif
  if (!strcmp(cls, "zwp_linux_buffer_params_v1")) {
    if(o&&nv_copy_enabled()&&!strcmp(method,"add")&&
       (o->nv_plane_fd||((a[4].u>>24)==3&&o->parent&&!o->parent->nv_nvidia))) {
      ++o->nv_planes;
      if(!o->nv_plane_fd&&a[1].u==0) {
        int fd=dup(a[0].i);
        if(fd>=0) {
          o->nv_plane_fd=fd+1;o->nv_stride=a[3].u;o->nv_offset=a[2].u;
          o->nv_modifier=((uint64_t)a[4].u<<32)|a[5].u;
        }
      }
      skip=1;
    }
    if(o&&o->nv_plane_fd&&!strcmp(method,"create_immed")) {
      nv_constructor=1;skip=1;
      if(o->nv_planes==1)nv_result=nv_create(o,a,interface);
    }
    if (!strcmp(method, "create") || !strcmp(method, "create_immed")) {
      if (o) o->height = a[2].i; /* remember h for the stride rewrite */
      FRAME_TRACE(4, "AnvilDroid", "dmabuf %s: %dx%d fmt=0x%x",
                          method, a[1].i, a[2].i, a[3].u);
    } else if (!strcmp(method, "add")) {
      /* The guest computes the dmabuf stride from its own view of the
       * image; the host renderer pads the width to a 256-byte pitch
       * multiple. Rewrite the stride from the fd's real size so the
       * compositor import uses the actual row pitch. */
      uint32_t real_stride = 0;
      if (resize_uses_nvidia() && o && o->height > 0 && a[1].u == 0 && a[2].u == 0) {
        /* freestanding: raw lseek(fd, 0, SEEK_END/SEEK_SET) on the dmabuf */
        long size;
        long back;
        __asm__ volatile("syscall" : "=a"(size) : "a"(8), "D"((long)a[0].i),
                         "S"(0), "d"(2) : "rcx", "r11", "memory");
        if (size > 0) {
          __asm__ volatile("syscall" : "=a"(back) : "a"(8), "D"((long)a[0].i),
                           "S"(0), "d"(0) : "rcx", "r11", "memory");
          if (back >= 0 && (uint64_t)size / (uint32_t)o->height <= UINT32_MAX)
            real_stride = (uint32_t)((uint64_t)size / (uint32_t)o->height);
        }
      }
      if (real_stride && real_stride != a[3].u) {
        __android_log_print(4, "AnvilDroid",
                            "dmabuf add: fd=%d stride %u -> %u (real)",
                            a[0].i, a[3].u, real_stride);
        a[3].u = real_stride;
      }
      if (o && a[1].u == 0 && a[2].u == 0)
        o->buffer_stride = a[3].u;
    }
  }
  struct proxy *temporary_region=0;
  full_ui_request(o,cls,method,a,&temporary_region);
  game_fit_request(o,cls,method,a,&temporary_region);
  if(!skip&&o&&!strcmp(cls,"wl_surface")&&!strcmp(method,"commit"))nv_commit(o);
  struct proxy *result = nv_constructor ? nv_result : skip ? 0 : modern
      ? real_array_flags(p, op, interface, version, flags, a)
      : real_marshal(p, op, a, interface, version);
  if(!skip&&o&&!strcmp(cls,"wl_surface")&&!strcmp(method,"commit"))csd_cursor_commit(o);
  if (!skip) window_feedback_event(o,cls,method);
  if (!skip && o && o->app && o->attached && !strcmp(cls,"wl_surface") &&
      !strcmp(method,"commit")) o->initial_presented=1;
  if(temporary_region){wayland_send(temporary_region,0,0);real_destroy(temporary_region);}
  if (original_identity) a[0].s = original_identity;
  if(o&&o->surface&&o->surface->full_ui&&!strcmp(cls,"xdg_surface")&&!strcmp(method,"ack_configure"))
    full_ui_apply(o->surface);
  /* Respond on the Wayland event thread, after acknowledging the configure.
   * This is a state-only commit; app buffers and Android tasks are untouched. */
  if (!strcmp(cls, "xdg_surface") && !strcmp(method, "ack_configure") &&
      o && o->surface && o->surface->app &&
      (o->surface->has_bounds || (o->surface->csd && o->surface->content_width))) {
    if (o->surface->csd && o->surface->csd->maximize_pending &&
        o->surface->csd->maximize_retry) {
      o->surface->csd->maximize_retry=0;
      wayland_send(o->surface->csd->top->p, 9, 0);
    }
    geometry(o->surface);
    if (!initial_layout_pending(o->surface)) {
      wayland_send(o->surface->p, 6, 0);
      if(o->surface->attached)o->surface->initial_presented=1;
    }
  }
  if (result && interface) {
    if (!strcmp(interface->name, "wp_viewporter")) scale_has_viewporter = 1;
    struct object *n = track(result);
    n->protocol_name = interface->name;
    n->protocol_interface = interface;
    n->display = o ? o->display : 0;
    if(!strcmp(interface->name,"wl_buffer")) {
      if(!strcmp(cls,"wl_shm_pool"))n->buffer_transport=1;
      else if(!strcmp(cls,"zwp_linux_buffer_params_v1"))n->buffer_transport=2;
      else if(!strcmp(cls,"wl_drm")||!strcmp(cls,"android_wlegl"))n->buffer_transport=3;
      if (o) n->buffer_stride = n->nv_ring?n->nv_ring->stride:o->buffer_stride;
    }
    if(!strcmp(interface->name,"zwp_linux_buffer_params_v1"))n->parent=o;
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
      request_window_decoration(n);
  }
  /* create_immed consumes the params' plane metadata. The resulting buffer
   * already owns our transport/stride copy. Legacy HWC may retain params
   * proxies indefinitely, so keeping them in every geometry lookup makes
   * frame handling progressively slower. Drop only our bookkeeping: the
   * caller still owns the protocol proxy and may destroy it later. */
  if (result && interface && !strcmp(interface->name, "wl_buffer") &&
      !strcmp(cls, "zwp_linux_buffer_params_v1") && !strcmp(method, "create_immed"))
    forget_proxy(p);
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
    keymap_capture_forget(old);
    if(old->nv_ring){nv_orphan(old->nv_ring);old->nv_ring=0;nv_trim();}
    if(old->nv_plane_fd)close(old->nv_plane_fd-1);
    if(old->full_ui){display_projection=(struct display_projection){0};display_pending_w=display_pending_h=0;}
    csd_cursor_destroy(old);
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
      if(o->attached_buffer==old->p)o->attached_buffer=0;
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
  keymap_capture_disconnect(display);
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
  for(struct nv_ring *r=nv_rings,*next;r;r=next){next=r->next;if(r->display==display)nv_destroy(r);}
  window_activation_disconnect(display);
  decoration_manager=decoration_bindings?decoration_bindings->manager:0;
  decoration_global_name=decoration_bindings?decoration_bindings->name:0;
  decoration_available=decoration_manager!=0;
  if (ime_display==display) {
    clipboard_disconnect();
    if (ime_text) {wayland_send(ime_text,0,0);real_destroy(ime_text);}
    if (ime_manager) {wayland_send(ime_manager,0,0);real_destroy(ime_manager);}
    ime_manager=ime_text=ime_seat=ime_surface=0;ime_display=0;
    ime_enabled=ime_serial=ime_manager_name=0;
    ime_state=(struct anvil_ime_state){0};
  }
}

/* Publish only complete requests; all callers run on the task worker. */
static int submit_resize_request(int task, int width, int height) {
  if (presentation_is_full()) return 0; // Full UI owns the complete Android canvas.
  char line[64];
  int length = format_resize_request(line, sizeof(line), task, width, height);
  if (length < 0) return 0;
  void *request = fopen("/data/waydroid_tmp/anvildroid/resize.tmp", "w");
  if (!request) return 0;
  int written = fprintf(request, "%s", line);
  int closed = fclose(request);
  char package[256]={0};
  pthread_mutex_lock(mutex);
  for (int i=0;i<task_count;++i)
    if (tasks[i].id==task) snprintf(package,sizeof(package),"%s",tasks[i].package);
  pthread_mutex_unlock(mutex);
  int retained=written==length && !closed && package[0] &&
      hwc_transition_call(1,task,package);
  int ok = written == length && !closed &&
      !rename("/data/waydroid_tmp/anvildroid/resize.tmp",
              "/data/waydroid_tmp/anvildroid/resize");
  if (!ok && retained) hwc_transition_call(4,task,package);
  if (!ok)
    __android_log_print(6, "AnvilDroid", "failed to publish resize task %d %dx%d",
                        task, width, height);
  return ok;
}

/* Initial bounds use the same coalesced request as a user resize. Publishing
 * directly here let the next stale snapshot's pitch correction overwrite the
 * requested height before Android had consumed it. */
static void queue_open_size(struct object *s) {
  s->resize.ready = 1;
  s->resize.width = task_to_logical(448, 1);
  s->resize.height = task_to_logical(704, 1);
  /* This is a one-shot initial layout, not an interactive resize. Waiting
   * for pointer quiet adds 300ms and makes the already visible window jump.
   * resize_due still refuses submission during a grab or cooldown. */
  s->resize.dirty = 0;
  s->resize.quiet_ticks = RESIZE_QUIET_TICKS;
  s->fit_w = s->resize.width;
  s->fit_h = s->resize.height;
  s->preview_width = s->preview_height = 0;
}

static int snap_resize_protected(struct object *s) {
  return game_fit_enabled(s) || s->resize.dragging || s->resize.width ||
    s->resize.cooldown || (s->resize.sent_width && window_fit_enabled(s) &&
    !window_fit_settled(s)) ||
    (!window_fit_enabled(s) && s->csd && s->csd->maximized);
}

/* The shell-UID companion supplies authoritative task bounds. It never calls
 * Wayland; the renderer thread consumes the snapshot on its next commit.
 */
static void *task_worker(void *unused) {
  (void)unused;
  int logged_scale = 0, count = 0;
  struct task snapshot[128];
  struct task_watch watch = {.fd = -1};
  for (;;) {
    if (task_watch_changed(&watch, "/data/waydroid_tmp/anvildroid")) {
      count = 0;
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
    }
    for (int i=0;i<count;++i) hwc_transition_call(5,snapshot[i].id,snapshot[i].package);
    hwc_transition_call(3,0,0);
    for (int i = 0; i < count; i++) resize_mode_load(snapshot[i].package);
    resize_mode_poll();
    pthread_mutex_lock(mutex);
    for(struct object *s=objects;s;s=s->next)
      if(s->app&&!s->initial_presented&&s->initial_wait_ticks<20)++s->initial_wait_ticks;
    int snapshot_changed=count!=task_count;
    for (int i = 0; i < count; i++) {
      int changed = logged_scale != hwc_scale_120 || i >= task_count ||
          tasks[i].id != snapshot[i].id || tasks[i].x != snapshot[i].x ||
          tasks[i].y != snapshot[i].y || tasks[i].width != snapshot[i].width ||
          tasks[i].height != snapshot[i].height ||
          strcmp(tasks[i].package, snapshot[i].package);
      snapshot_changed|=changed;
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
    for(struct object *s=objects;s;s=s->next)if(s->full_ui&&s->custom_size.enabled) {
      int w,h,pw=display_projection.pw?display_projection.pw:logical_to_task(s->full_canvas_w);
      int ph=display_projection.ph?display_projection.ph:logical_to_task(s->full_canvas_h);
      if(custom_size_due(&s->custom_size,pw,ph,&w,&h)&&
         (w!=display_projection.lw||h!=display_projection.lh))display_size_request(w,h,1);
    }
    int full_present=0;for(struct object *s=objects;s;s=s->next)if(s->full_ui)full_present=1;
    if(full_present&&display_size_refresh())for(struct object *s=objects;s;s=s->next)
      if(s->full_ui) {
        if(!s->custom_size.enabled){s->full_w=display_projection.lw;s->full_h=display_projection.lh;}full_ui_apply(s);
        if(real_display_flush&&s->display)real_display_flush(s->display);
        __android_log_print(4,"AnvilDroidCsd","Size applied %dx%d projection=%d,%d %dx%d",
          display_projection.lw,display_projection.lh,display_projection.x,display_projection.y,display_projection.w,display_projection.h);
      }
    if(snapshot_changed)for(struct object *s=objects;s;s=s->next)
      if(s->full_ui&&(s->hints||s->overlay))keymap_wake();
    pthread_mutex_unlock(mutex);
    /* First sight of a task: shrink Android's default freeform (~512x824)
     * to a desktop-friendly open size. One shot per task id; user resizes
     * afterwards are never re-clamped. */
    static struct open_size_history opened;
    int task_ids[128], first_open[128]={0}, opening[128]={0};
    for (int i=0;i<count;++i) task_ids[i]=tasks[i].id;
    open_size_snapshot(&opened,task_ids,count,first_open);
    for (int i = 0; i < count && !presentation_is_full(); i++) {
      if (!first_open[i]) continue;
      if (resize_mode_worker_fixed(tasks[i].package)) continue;
      if (tasks[i].width <= 448 && tasks[i].height <= 704)
        continue;
      /* A surface recreated by a resize must not be re-clamped: fragile
       * apps replace their task while relaying out, which would snap the
       * window back to the default size in a loop. */
      pthread_mutex_lock(mutex);
      int recreated = profile_recreated(tasks[i].package);
      pthread_mutex_unlock(mutex);
      if (recreated) continue;
      /* Only the first-ever launch of a package gets the default open size;
       * afterwards the per-package profile (the user's last resized size)
       * owns the restore. */
      {
        const char *rid = getenv("ANVILDROID_RUNTIME_ID");
        if (!rid || !*rid) rid = "existing";
        int dir = open(PROFILE_ROOT, PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
        if (dir >= 0) {
          int saved_w = 0, saved_h = 0;
          int found = profile_read(dir, tasks[i].package, rid, &saved_w, &saved_h);
          close(dir);
          if (found != 0) continue; /* Preserve invalid/unreadable saved files too. */
        }
      }
      pthread_mutex_lock(mutex);
      for (struct object *o = objects; o; o = o->next)
        if (o->app && o->app_id && !strcmp(o->app_id, tasks[i].package)) {
          queue_open_size(o);
          opening[i]=1;
        }
      pthread_mutex_unlock(mutex);
      if (!opening[i]) open_size_defer(&opened, tasks[i].id);
    }
    /* Hybrid NVIDIA: snap app window widths to 64-pixel multiples so the
     * surface pitch is 256-byte-aligned (radeonsi's LINEAR dmabuf import
     * requirement). The companion applies the resize; the next task
     * snapshot reports the aligned source inside the unchanged host frame. */
    static int last_snap_task = -1, last_snap_width = 0;
    for (int i = 0; resize_uses_nvidia() && i < count && !presentation_is_full(); i++) {
      if (resize_mode_worker_fixed(tasks[i].package)) continue;
      if (tasks[i].width < 64 || (tasks[i].width & 63) == 0)
        continue;
      if (tasks[i].id == last_snap_task && tasks[i].width == last_snap_width)
        continue;
      /* The open-size request already owns this task's initial bounds. */
      if (opening[i]) continue;
      /* Respect Keep resolution and never overwrite a user resize.
       * With viewporter, aligned content fits inside the exact host frame. */
      int protected = 0;
      pthread_mutex_lock(mutex);
      for (struct object *o = objects; o; o = o->next)
        if (o->app && o->app_id && !strcmp(o->app_id, tasks[i].package) &&
            snap_resize_protected(o))
          protected = 1;
      pthread_mutex_unlock(mutex);
      if (protected) continue;
      int snapped = resize_task_width(tasks[i].width, 1);
      if (submit_resize_request(tasks[i].id, snapped, tasks[i].height)) {
        __android_log_print(4, "AnvilDroid",
                            "snap resize task %d %dx%d -> %dx%d (pitch)",
                            tasks[i].id, tasks[i].width, tasks[i].height,
                            snapped, tasks[i].height);
        last_snap_task = tasks[i].id;
        last_snap_width = tasks[i].width;
      }
    }
    window_feedback_poll();
    profile_poll();
    /* A static app can stop committing while the IO worker reads its
     * profile. Publish the pending first frame once ready (or timed out),
     * without requiring another Android redraw to make it visible. */
    pthread_mutex_lock(mutex);
    for(struct object *s=objects;s;s=s->next) {
      if(!s->app||s->initial_presented||!s->attached||!s->has_bounds||initial_layout_pending(s))continue;
      geometry(s);
      if(initial_layout_pending(s))continue;
      wayland_send(s->p,6,0);
      s->initial_presented=1;
      if(real_display_flush&&s->display)real_display_flush(s->display);
    }
    pthread_mutex_unlock(mutex);
    profile_state_poll();
    close_poll();
    keymap_io_poll();
    pthread_mutex_lock(mutex);
    if((clip_dirty && clip_device && clip_serial && ime_surface) ||
       (ime_dirty && ime_text))keymap_wake();
    pthread_mutex_unlock(mutex);
    int id = -1, width = 0, height = 0, resize_noop = 0;
    char persist_package[256] = "";
    int persist_w = 0, persist_h = 0;
    pthread_mutex_lock(mutex);
    for (struct object *s = objects; s; s = s->next) {
      /* Configures queued around a surface replacement may arrive even
       * after the first maximized configure. Protect the recovery interval,
       * then let ordinary compositor/user state changes through again. */
      if (!s->app || !s->app_id || game_fit_enabled(s))
        continue;
      if (s->csd && s->csd->maximize_pending>0) --s->csd->maximize_pending;
      if (!resize_due(&s->resize) || id >= 0)
        continue;
      int matches = 0, task_width = 0, task_height = 0;
      for (int i = 0; i < count; i++) {
        if (!strcmp(s->app_id, tasks[i].package)) {
          id = tasks[i].id;
          task_width = tasks[i].width; task_height = tasks[i].height;
          matches++;
        }
      }
      if (matches == 1) {
        width = logical_to_task(s->resize.width);
        /* NVIDIA aligns the task down; the shared viewport fills the accepted
         * host frame once that importable task has finished relayout. */
        width = resize_task_width(width, resize_uses_nvidia());
        height = logical_to_task(s->resize.height);
        resize_noop = width == task_width && height == task_height;
        /* Changes within one aligned-width bucket only adjust the side bars;
         * recreating an Android activity would do no useful work. */
        if (!resize_noop) profile_note_resize(s->app_id, id);
        snprintf(persist_package, sizeof(persist_package), "%s", s->app_id);
        /* Initial compositor configures can precede the profile read. They
         * must not replace the saved user size before it can be restored. */
        if (!s->resize.maximized && s->profile_loaded &&
            !s->profile_restoring && !s->profile_recreated) {
          persist_w = s->resize.width;
          persist_h = s->resize.height;
        }
      } else {
        id = -1;
      }
      if (matches == 1) {
        if (window_fit_enabled(s)) {
          s->fit_delivered_w = s->resize.width;
          s->fit_delivered_h = s->resize.height;
          /* A resize in the same pitch bucket needs no Android relayout.
           * Its settled fit still changes: an idle task may not commit a
           * new frame to remove the transient contain bars for us. */
          if (resize_noop) {
            geometry(s);
            if(!initial_layout_pending(s)) {
              wayland_send(s->p, 6, 0);
              if(s->attached)s->initial_presented=1;
            }
            if (real_display_flush && s->display) real_display_flush(s->display);
          }
        }
        if (resize_noop) s->resize.width = s->resize.height = 0;
        else resize_submitted(&s->resize);
      }
      else s->resize.width = s->resize.height = 0;
    }
    pthread_mutex_unlock(mutex);
    if (id >= 0 && width >= 64 && height >= 64 && width <= 8192 &&
        height <= 8192) {
      int submitted = resize_noop || submit_resize_request(id, width, height);
      if (submitted && !resize_noop) {
        __android_log_print(4, "AnvilDroid",
                            "resize task %d physical=%dx%d scale=%d/120 after quiet interval",
                            id, width, height, hwc_scale_120);
      }
      /* Persist the size right away: the user may relaunch before the
       * settle-based profile poll has confirmed the new content. */
      if (submitted && persist_package[0] && persist_w >= 64 && persist_h >= 64) {
        const char *rid = getenv("ANVILDROID_RUNTIME_ID");
        if (!rid || !*rid) rid = "existing";
        int dir = open(PROFILE_ROOT, PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
        if (dir >= 0) {
          int saved=profile_write(dir, persist_package, rid, persist_w, persist_h);
          close(dir);
          __android_log_print(saved?4:5, "AnvilDroid", "%s profile %s %dx%d",
                              saved?"persist":"failed to persist",persist_package, persist_w, persist_h);
        }
      }
    }
    usleep(RESIZE_POLL_US);
  }
  return 0;
}
