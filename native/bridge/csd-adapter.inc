/* Experimental in-process CSD. Android retains its one xdg_toplevel and its
 * buffers. These four wl_shm subsurfaces belong to that same window, not to a
 * second host window. No glibc/libdecor is loaded into Android's bionic HWC.
 * All functions execute under bridge mutex except original listener calls. */
#include "csd-layout.h"
#include "csd-style.h"
#include "csd-cursor.h"
#ifndef ANVIL_CSD_EXPERIMENT
#define ANVIL_CSD_EXPERIMENT 0
#endif
extern long syscall(long, ...);
extern int ftruncate(int, long);
extern void *mmap(void *, size_t, int, int, int, long);
extern int munmap(void *, size_t);
extern int close(int);
static void csd_cursor_show(struct object *);
static void keymap_overlay_sync(struct object *,int,int,int,int);
static void keymap_overlay_destroy(struct object *);
static int keymap_overlay_enter(struct object *,struct proxy *);
static void keymap_overlay_button(struct object *,uint32_t,uint32_t);
static void keymap_overlay_motion(struct object *);
static void keymap_overlay_pointer_cancel(struct object *);
static void keymap_overlay_paint(uint32_t *,int,int);
static int close_request_app(struct object *);
static void full_ui_box(struct object *,int *,int *,int *,int *);
static int profile_recreated(const char *);
struct csd_frame;
static int profile_restore_frame(const char *, struct csd_frame *);
#include "keymap-font.h"

struct csd_piece {
  struct proxy *surface, *subsurface;
  int width, height;
};
struct csd_frame {
  struct object *top;
  struct csd_piece pieces[4]; /* Client frame only; no side toolbar. */
  struct csd_piece bars[4];   /* Full-UI letterbox covers: opaque black. */
  int fullscreen, maximized, paint_maximized;
  int maximize_pending;
  int maximize_retry;
  int normal_pending;
  int restore_width, restore_height;
  int max_width, max_height;  /* The compositor's maximized workarea. */
  struct csd_piece menu;
  int menu_open, menu_hover, menu_pressed, menu_submenu, menu_scroll;
  int size_pending; /* Preserve a Size selection through unset_maximized. */
  int menu_x, menu_y;
  int hover, pressed;
  uint32_t header_click_time;
  int header_click_valid, header_click_x, header_click_y;
};
static void csd_menu_sync(struct csd_frame *,int,int,int,int);
static void csd_menu_close(struct csd_frame *);
static void csd_menu_toggle(struct csd_frame *);
static void csd_menu_destroy(struct csd_frame *);
static int csd_menu_enter(struct object *,struct proxy *);
static void csd_menu_motion(struct object *);
static void csd_menu_scroll(struct object *,int);
static void csd_menu_button(struct object *,uint32_t,uint32_t);
static int csd_menu_key(struct object *,uint32_t,uint32_t);
static void csd_menu_paint(uint32_t *,int,int);
static struct csd_frame *csd_menu_paint_context;
static int csd_frame_hit(struct csd_frame *f,int part,int x,int y,int w,int h) {
  if(f->fullscreen)return 0;
  if(part==0 && (f->top->surface->app||f->top->surface->full_ui) && y>=CSD_GRIP && y<h &&
     x>=CSD_BORDER && x<CSD_BORDER+CSD_BUTTON)return CSD_MENU;
  return csd_hit(part,x,y,w,h);
}
struct csd_buffer { struct proxy *proxy; struct csd_buffer *next; };
static struct csd_buffer *csd_buffers;
static struct proxy *csd_compositor, *csd_subcompositor, *csd_shm;
static const struct interface *csd_surface_i, *csd_subsurface_i, *csd_pool_i, *csd_buffer_i;
static const struct interface *csd_region_i;
static void csd_track_global(struct proxy *p, const struct interface *iface) {
  if (!ANVIL_CSD_EXPERIMENT) return;
  /* HWC statically links libwayland: core interface symbols are hidden. Borrow
   * its live globals and canonical constructor type tables (public ABI), not
   * dlsym or a second connection/event queue. Never own their listeners. */
  if (!strcmp(iface->name,"wl_compositor")) {
    csd_compositor=p; csd_surface_i=iface->methods[0].types[0];
    csd_region_i=iface->methods[1].types[0];
  }
  if (!strcmp(iface->name,"wl_subcompositor")) {
    csd_subcompositor=p; csd_subsurface_i=iface->methods[1].types[0];
  }
  if (!strcmp(iface->name,"wl_shm")) {
    csd_shm=p; csd_pool_i=iface->methods[0].types[0];
    csd_buffer_i=csd_pool_i->methods[0].types[0];
  }
}
static void csd_release(void *data, struct proxy *p) {
  pthread_mutex_lock(mutex);
  struct csd_buffer **at = &csd_buffers;
  while (*at && *at != data) at = &(*at)->next;
  if (*at) {
    struct csd_buffer *old = *at;
    *at = old->next;
    wayland_send(p, 0, 0);
    real_destroy(p);
    free(old);
  }
  pthread_mutex_unlock(mutex);
}
static void (*csd_buffer_listener[])(void) = {(void (*)(void))csd_release};
static struct proxy *csd_paint(int part, int w, int h, int state) {
  if (w < 1 || h < 1 || w > 8240 || h > 8240) return 0;
  if ((part==4 || part==6) && (int64_t)w*h>16777216) return 0;
  /* memfd_create on the bridge's supported x86_64 Android ABI. */
  int fd = (int)syscall(319, "anvildroid-csd", 1);
  if (fd < 0) return 0;
  size_t size = (size_t)w * h * 4;
  if (ftruncate(fd, (long)size)) { close(fd); return 0; }
  uint32_t *pixels = mmap(0, size, 3, 1, fd, 0);
  if (pixels == (void *)-1) { close(fd); return 0; }
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
    uint32_t color = 0;
    if(part==4) {
      color=0x24050910; /* Subtle premultiplied scrim; app stays legible. */
    }
    if(part==7)color=csd_cursor_pixel(x,y);
    if((part>=1&&part<=3)||part==8)color=csd_edge_pixel(part,x,y,w,h,state);
    if(part==5)color=0xff000000; /* Letterbox, not a toolbar. */
    if (part == 0) color = csd_header_pixel(x,y,w,h,state);
    pixels[(size_t)y*w+x] = color;
  }
  if(part==4)keymap_overlay_paint(pixels,w,h);
  if(part==6)csd_menu_paint(pixels,w,h);
  union argument pool_a[3] = {{.o=0}, {.i=fd}, {.i=(int)size}};
  struct proxy *pool = real_marshal(csd_shm, 0, pool_a, csd_pool_i, 1);
  struct proxy *buffer = 0;
  if (pool) {
    union argument a[6] = {{.o=0},{.i=0},{.i=w},{.i=h},{.i=w*4},{.u=0}};
    buffer = real_marshal(pool, 0, a, csd_buffer_i, 1);
    wayland_send(pool, 1, 0);
    real_destroy(pool);
  }
  munmap(pixels, size);
  close(fd);
  if (buffer) {
    struct csd_buffer *b = calloc(1, sizeof(*b));
    if (!b) abort();
    b->proxy = buffer; b->next = csd_buffers; csd_buffers = b;
    real_listen(buffer, csd_buffer_listener, b);
  }
  return buffer;
}
static void csd_create(struct object *top) {
  if (!ANVIL_CSD_EXPERIMENT || !top || top->csd ||
      (top->decoration && top->decoration_mode != 1) ||
      !top->surface || (!top->surface->app && !top->surface->full_ui) ||
      !csd_compositor || !csd_subcompositor || !csd_shm)
    return;
  struct csd_frame *f = calloc(1, sizeof(*f));
  if (!f) abort();
  f->top = top;
  f->normal_pending = top->surface->app && !top->surface->full_ui;
  for (int i = 0; i < 4; i++) {
    union argument a = {.o=0};
    f->pieces[i].surface = real_marshal(csd_compositor, 0, &a, csd_surface_i, 1);
    union argument sub[3] = {{.o=0},{.o=f->pieces[i].surface},{.o=top->surface->p}};
    f->pieces[i].subsurface = real_marshal(csd_subcompositor, 1, sub, csd_subsurface_i, 1);
    if (!f->pieces[i].surface || !f->pieces[i].subsurface) abort();
    /* Default synchronized mode: frame and Android geometry latch together. */
  }
  if (top->surface->full_ui) {
    /* The canvas surface background is transparent: letterbox bars must be
     * opaque black pieces of their own. */
    for (int i = 0; i < 4; i++) {
      union argument a = {.o=0};
      f->bars[i].surface = real_marshal(csd_compositor, 0, &a, csd_surface_i, 1);
      union argument sub[3] = {{.o=0},{.o=f->bars[i].surface},{.o=top->surface->p}};
      f->bars[i].subsurface = real_marshal(csd_subcompositor, 1, sub, csd_subsurface_i, 1);
      if (!f->bars[i].surface || !f->bars[i].subsurface) abort();
    }
  }
  top->csd = f;
  top->surface->csd = f;
  {
    /* The window frame follows the drag through configure acks; Android only
     * receives the final size on release. Live task resizes during a drag
     * hammer fragile apps (Cocos/ARM games) with repeated relayouts and kill
     * them; the frame still tracks the pointer either way. */
    top->surface->resize.live_drag = 0;
    top->surface->resize.ready = 1;
    /* HWC requests maximize before publishing app_id. Undo that pending request
     * before its initial commit; calibration surfaces are excluded above.
     * A surface recreated by a resize must keep the user's maximize state,
     * or fragile apps need two clicks to maximize. */
    if (profile_restore_frame(top->surface->app_id, f)) {
      top->surface->resize.maximized = f->maximized;
      if(top->surface->app && !top->surface->full_ui) {
        struct object *s=top->surface;
        s->fit_w=f->maximized?f->max_width-2*CSD_BORDER:f->restore_width;
        s->fit_h=f->maximized?f->max_height-CSD_HEADER-CSD_BORDER:f->restore_height;
        s->fit_delivered_w=s->fit_w;s->fit_delivered_h=s->fit_h;
      }
      f->maximize_pending = f->maximized ? 40 : 0;
      f->normal_pending = !f->maximized;
      wayland_send(top->p, f->maximized ? 9 : 10, 0);
      __android_log_print(4, "AnvilDroidCsd",
                          "restore recreated frame %s maximized=%d normal=%dx%d",
                          top->surface->app_id, f->maximized,
                          f->restore_width, f->restore_height);
    } else if (!profile_recreated(top->surface->app_id))
      wayland_send(top->p, 10, 0);
    union argument minimum[2] = {{.i=160+2*CSD_BORDER},{.i=64+CSD_HEADER+CSD_BORDER}};
    wayland_send(top->p, 8, minimum);
    __android_log_print(4, "AnvilDroidCsd", "real CSD attached to %s", top->surface->app_id);
  }
}
/* Find the xdg_toplevel proxy owning this surface (F11 fullscreen target). */
static struct proxy *csd_toplevel(struct object *s) {
  if (!s) return 0;
  for (struct object *o = objects; o; o = o->next)
    if (o->surface == s && o->p && o->p->interface &&
        !strcmp(o->p->interface->name, "xdg_toplevel")) return o->p;
  return 0;
}
static int csd_input(struct proxy *surface, int x, int y, int w, int h) {
  if (!csd_compositor || !csd_region_i) return 0;
  union argument id = {.o=0};
  struct proxy *region=real_marshal(csd_compositor,1,&id,csd_region_i,1);
  if (!region) return 0;
  union argument rect[4]={{.i=x},{.i=y},{.i=w},{.i=h}};
  if(w>0&&h>0)wayland_send(region,1,rect);
  union argument input={.o=region};
  wayland_send(surface,5,&input);
  wayland_send(region,0,0);
  real_destroy(region);
  return 1;
}
static void csd_sync(struct object *s, int x, int y, int w, int h) {
  struct csd_frame *f = s->csd;
  if (!f) return;
  if (f->normal_pending && !f->maximized && !f->restore_width && w>=64 && h>=64) {
    f->restore_width=w;f->restore_height=h;
  }
  int repaint_frame=f->paint_maximized!=f->maximized;
  /* Extend only transparent input surfaces outside the 1px xdg frame.
   * Keep visual header and configure/restore dimensions exactly as before. */
  int widths[4] = {w+2*CSD_BORDER,CSD_RESIZE,CSD_RESIZE,w+2*CSD_RESIZE};
  int heights[4] = {CSD_HEADER,h+CSD_HEADER+CSD_RESIZE,h+CSD_HEADER+CSD_RESIZE,CSD_RESIZE};
  /* Put chrome above the content origin. Clamping the header to zero
   * overlays the app's first rows, especially on maximized windows. The
   * xdg geometry includes this negative surface-local header extent. */
  int xs[4] = {x-CSD_BORDER,x-CSD_RESIZE,x+w,x-CSD_RESIZE};
  int ys[4] = {y-CSD_HEADER,y-CSD_HEADER,y-CSD_HEADER,y+h};
  /* The maximized window can be taller than the canvas surface: extend the
   * bottom piece over the uncovered strip so it reads black, not see-through. */
  int overflow = 0;
  if (s->full_ui && s->full_canvas_h > 0 && h > s->full_canvas_h)
    overflow = h - s->full_canvas_h;
  if (overflow) {
    heights[3] = CSD_RESIZE + overflow;
    ys[3] = y + h - overflow;
  }
  if (!s->full_ui) csd_input(s->p,x,y,w,h);
  struct proxy *above = 0;
  for (struct object *sub=objects; sub; sub=sub->next) {
    if (sub->parent != s || !sub->surface || !object_is(sub,"wl_subsurface")) continue;
    /* HWC appends new layers above old siblings. objects is newest-first. */
    if (!above) above=sub->surface->p;
    /* The full-UI canvas scales its own children and rewrites their input
     * regions; never override them here. */
    if(!(window_fit_enabled(s) && s->fit) && !s->full_ui)csd_input(sub->surface->p,
      x-sub->x-sub->surface->clip_x,y-sub->y-sub->surface->clip_y,
      sub->surface->clip_hidden?0:w,sub->surface->clip_hidden?0:h);
    if (!(window_fit_enabled(s) && s->fit) && !s->full_ui && csd_compositor && csd_region_i)
      wayland_send(sub->surface->p,6,0); /* Fit already committed its transformed child. */
  }
  for (int i=0; i<4; i++) {
    struct csd_piece *p = &f->pieces[i];
    if (!p->surface) continue; /* SSD mode has no client frame chrome */
    union argument pos[2] = {{.i=xs[i]}, {.i=ys[i]}};
    wayland_send(p->subsurface, 1, pos);
    if (above) {
      union argument sibling={.o=above};
      wayland_send(p->subsurface,2,&sibling);
    }
    if (f->fullscreen) {
      union argument empty[3] = {{.o=0},{.i=0},{.i=0}};
      wayland_send(p->surface, 1, empty);
      p->width = p->height = 0;
      csd_input(p->surface,0,0,0,0);
    } else if (p->width != widths[i] || p->height != heights[i] || repaint_frame) {
      /* The overflow strip uses the letterbox black, not the frame color. */
      struct proxy *buffer = csd_paint(i == 3 && overflow ? 8 : i,
        widths[i], heights[i],
        (csd_style_state(f->maximized,f->hover,f->pressed) | ((s->app||s->full_ui) ? 2 : 0)));
      if (!buffer) continue;
      union argument attach[3] = {{.o=buffer},{.i=0},{.i=0}};
      wayland_send(p->surface, 1, attach);
      union argument damage[4] = {{.i=0},{.i=0},{.i=widths[i]},{.i=heights[i]}};
      wayland_send(p->surface, 2, damage);
      p->width = widths[i]; p->height = heights[i];
      csd_input(p->surface,0,0,widths[i],heights[i]);
    }
    wayland_send(p->surface, 6, 0);
  }
  if (s->full_ui && f->bars[0].surface) {
    /* Opaque black letterbox bars around the aspect-fitted canvas box. */
    int fw, fh, ox, oy;
    full_ui_box(s, &fw, &fh, &ox, &oy);
    struct csd_bar { int x, y, w, h; } rects[4] = {
      {0, 0, ox, h},                    /* left */
      {ox + fw, 0, w - ox - fw, h},     /* right */
      {ox, 0, fw, oy},                  /* top */
      {ox, oy + fh, fw, h - oy - fh}};  /* bottom */
    for (int i = 0; i < 4; i++) {
      struct csd_piece *b = &f->bars[i];
      int bw = rects[i].w > 0 ? rects[i].w : 0;
      int bh = rects[i].h > 0 ? rects[i].h : 0;
      union argument pos[2] = {{.i=rects[i].x},{.i=rects[i].y}};
      wayland_send(b->subsurface, 1, pos);
      if (above) {
        union argument sibling = {.o=above};
        wayland_send(b->subsurface, 2, &sibling);
      }
      if (bw > 0 && bh > 0) {
        if (b->width != bw || b->height != bh) {
          struct proxy *buffer = csd_paint(5, bw, bh, 0);
          if (buffer) {
            union argument attach[3] = {{.o=buffer},{.i=0},{.i=0}};
            wayland_send(b->surface, 1, attach);
            union argument damage[4] = {{.i=0},{.i=0},{.i=bw},{.i=bh}};
            wayland_send(b->surface, 2, damage);
            b->width = bw; b->height = bh;
          }
        }
        wayland_send(b->surface, 6, 0);
      } else {
        union argument empty[3] = {{.o=0},{.i=0},{.i=0}};
        wayland_send(b->surface, 1, empty);
        b->width = b->height = 0;
        wayland_send(b->surface, 6, 0); /* The detach needs its own commit. */
      }
    }
  }
  f->paint_maximized=f->maximized;
  csd_menu_sync(f,x,y,w,h);
  /* Present the pieces without waiting for the next HWC frame: the canvas
   * (and idle apps) commit sparsely, and synchronized subsurfaces only
   * surface their new buffers on a parent commit. */
  wayland_send(s->p, 6, 0);
}
static void csd_repaint_header(struct csd_frame *f) {
  struct csd_piece *p=&f->pieces[0];
  __android_log_print(4, "AnvilDroidCsd", "repaint header max=%d hover=%d pressed=%d",
                      f->maximized, f->hover, f->pressed);
  if (f->fullscreen || !p->surface || p->width<1 || !csd_shm) return;
  struct proxy *buffer=csd_paint(0,p->width,p->height,
    (csd_style_state(f->maximized,f->hover,f->pressed) | ((f->top->surface->app||f->top->surface->full_ui) ? 2 : 0)));
  if (!buffer) return;
  union argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
  union argument damage[4]={{.i=0},{.i=0},{.i=p->width},{.i=p->height}};
  wayland_send(p->surface,1,attach);
  wayland_send(p->surface,2,damage);
  wayland_send(p->surface,6,0);
  /* The header is synchronized with its parent; idle Android apps may not
   * submit another frame to make hover feedback visible. */
  if (f->top && f->top->surface) wayland_send(f->top->surface->p,6,0);
}
static void csd_feedback(struct csd_frame *f, int hover, int pressed) {
  if (!f || (f->hover == hover && f->pressed == pressed)) return;
  f->hover=hover; f->pressed=pressed;
  csd_repaint_header(f);
}
static void csd_pointer_feedback(struct object *o) {
  struct csd_frame *f=o->csd_focus;
  if (!f) return;
  struct csd_piece *p=&f->pieces[o->csd_part];
  int action=csd_frame_hit(f,o->csd_part,o->csd_x,o->csd_y,p->width,p->height);
  csd_feedback(f,action>=CSD_MINIMIZE?action:0,
    o->csd_pressed>=CSD_MINIMIZE?o->csd_pressed:0);
}
static void csd_destroy(struct object *o) {
  if (!o || !o->csd) return;
  struct csd_frame *f = o->csd;
  csd_menu_destroy(f);
  for (struct object *at=objects; at; at=at->next) {
    if (at->menu_focus == f) {at->menu_focus=0;at->csd_suppress=1;}
    if (at->csd == f) at->csd = 0;
    if (at->csd_resize == f) at->csd_resize = 0;
    if (at->csd_focus == f) { at->csd_focus = 0; at->csd_suppress = 1; }
  }
  for (int i=0; i<4; i++) {
    if (!f->pieces[i].surface) continue;
    wayland_send(f->pieces[i].subsurface, 0, 0);
    real_destroy(f->pieces[i].subsurface);
    wayland_send(f->pieces[i].surface, 0, 0);
    real_destroy(f->pieces[i].surface);
  }
  for (int i=0; i<4; i++) {
    if (!f->bars[i].surface) continue;
    wayland_send(f->bars[i].subsurface, 0, 0);
    real_destroy(f->bars[i].subsurface);
    wayland_send(f->bars[i].surface, 0, 0);
    real_destroy(f->bars[i].surface);
  }
  free(f);
}
static void csd_resize_finished(struct object *pointer) {
  struct csd_frame *frame=pointer->csd_resize;
  pointer->csd_resize=0;
  if (!frame || !frame->top || !frame->top->surface) return;
  struct object *surface=frame->top->surface;
  resize_release(&surface->resize);
  surface->custom_size.dragging=0;
  __android_log_print(4,"AnvilDroidCsd","resize grab ended %dx%d",
                      surface->resize.width,surface->resize.height);
}
static void csd_pointer_enter(void *data, struct proxy *p, uint32_t serial,
                               struct proxy *surface, int32_t x, int32_t y) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  o->cursor_serial=serial;
  clipboard_input(serial);
  /* A compositor-owned resize consumes the release. Its subsequent enter
   * ends that grab, even when the pointer returns to Android content or a
   * different window. The transient header focus is already gone. */
  csd_resize_finished(o);
  csd_feedback(o->csd_focus,0,0);
  o->csd_focus = 0; o->csd_suppress = 0; o->csd_pressed = 0;
  o->overlay_focus=0;o->overlay_pressed=0;
  o->menu_focus=0;
  o->pointer_surface=surface;
  o->fit_bar_focus=game_fit_bar(surface);
  if(o->fit_bar_focus){o->csd_suppress=1;pthread_mutex_unlock(mutex);return;}
  for (struct object *at=objects; at; at=at->next) if (at->csd) {
    for (int i=0; i<4; i++) if (at->csd->pieces[i].surface == surface) {
      o->csd_focus=at->csd; o->csd_part=i; o->csd_suppress=1;
    }
  }
  o->csd_x=x/256; o->csd_y=y/256;
  csd_pointer_feedback(o);
  if(keymap_overlay_enter(o,surface))o->csd_suppress=1;
  if(csd_menu_enter(o,surface))o->csd_suppress=1;
  x=content_clip_input(surface,x,0);y=content_clip_input(surface,y,1);
  x=full_ui_input(surface,x,0);y=full_ui_input(surface,y,1);
  x=game_fit_input(surface,x,0);y=game_fit_input(surface,y,1);
  pthread_mutex_unlock(mutex);
  /* Upstream enter only updates focus/cursor, never injects Android input. */
  ((void (*)(void *,struct proxy *,uint32_t,struct proxy *,int32_t,int32_t))o->listener[0])(
    o->listener_data,p,serial,surface,x,y);
  pthread_mutex_lock(mutex);
  csd_cursor_show(o);
  pthread_mutex_unlock(mutex);
}
static void csd_pointer_leave(void *data, struct proxy *p, uint32_t serial, struct proxy *surface) {
  struct object *o=data;
  pthread_mutex_lock(mutex);
  int was_bar=o->fit_bar_focus;o->fit_bar_focus=0;
  keymap_overlay_pointer_cancel(o);
  csd_feedback(o->csd_focus,0,0);
  o->pointer_surface=0;o->cursor_serial=0;
  o->csd_focus=0; o->csd_pressed=0;
  o->overlay_focus=0;o->overlay_pressed=0;
  o->menu_focus=0;
  /* Keep suppress through trailing frame/axis events, until next enter. */
  pthread_mutex_unlock(mutex);
  if(was_bar)return;
  ((void (*)(void *,struct proxy *,uint32_t,struct proxy *))o->listener[1])(o->listener_data,p,serial,surface);
}
static void csd_pointer_motion(void *data, struct proxy *p, uint32_t time, int32_t x, int32_t y) {
  struct object *o=data;
  pthread_mutex_lock(mutex);
  o->csd_x=x/256; o->csd_y=y/256;
  csd_pointer_feedback(o);
  if(o->overlay_focus)keymap_overlay_motion(o);
  if(o->menu_focus)csd_menu_motion(o);
  int suppress=o->csd_suppress;
  x=content_clip_input(o->pointer_surface,x,0);y=content_clip_input(o->pointer_surface,y,1);
  x=full_ui_input(o->pointer_surface,x,0);y=full_ui_input(o->pointer_surface,y,1);
  x=game_fit_input(o->pointer_surface,x,0);y=game_fit_input(o->pointer_surface,y,1);
  pthread_mutex_unlock(mutex);
  if (!suppress) ((void (*)(void *,struct proxy *,uint32_t,int32_t,int32_t))o->listener[2])(o->listener_data,p,time,x,y);
}
static void csd_toggle_maximize(struct csd_frame *f) {
  f->maximize_pending=0;
  f->maximize_retry=0;
  wayland_send(f->top->p,f->maximized?10:9,0);
  if (!f->maximized && f->top->surface) {
    struct object *s=f->top->surface;
    f->restore_width=s->preview_width?s->preview_width:s->content_width;
    f->restore_height=s->preview_height?s->preview_height:s->content_height;
  }
  f->maximized=!f->maximized;
  if (f->top->surface) f->top->surface->profile_state_dirty=1;
  csd_repaint_header(f);
}
static int csd_header_double_click(struct csd_frame *f,uint32_t time,int x,int y) {
  int dx=x-f->header_click_x,dy=y-f->header_click_y;
  if (dx<0) dx=-dx;
  if (dy<0) dy=-dy;
  int twice=f->header_click_valid && time-f->header_click_time<=400 && dx<=5 && dy<=5;
  f->header_click_valid=!twice;
  f->header_click_time=time;f->header_click_x=x;f->header_click_y=y;
  return twice;
}
static void csd_pointer_button(void *data, struct proxy *p, uint32_t serial, uint32_t time,
                               uint32_t button, uint32_t state) {
  struct object *o=data;
  void (*closed)(void *,struct proxy *)=0;
  void *closed_data=0; struct proxy *closed_proxy=0;
  pthread_mutex_lock(mutex);
  clipboard_input(serial);
  int suppress=o->csd_suppress;
  struct csd_frame *f=o->csd_focus;
  if (button==0x110 && state==0) csd_resize_finished(o);
  if(o->overlay_focus)keymap_overlay_button(o,button,state);
  if(o->menu_focus)csd_menu_button(o,button,state);
  if (f && button==0x110) {
    struct csd_piece *piece=&f->pieces[o->csd_part];
    int action=csd_frame_hit(f,o->csd_part,o->csd_x,o->csd_y,piece->width,piece->height);
    if (state==1) {
      o->csd_pressed=action;
      if (action!=CSD_MOVE) f->header_click_valid=0;
      if (action==CSD_MOVE && csd_header_double_click(f,time,o->csd_x,o->csd_y)) {
        o->csd_pressed=0;
        csd_toggle_maximize(f);
      } else if (o->parent && (action==CSD_MOVE || (action>0 && action<16 && !f->maximized))) {
        if (action!=CSD_MOVE) o->csd_resize=f;
        union argument a[3]={{.o=o->parent->p},{.u=serial},{.u=(uint32_t)action}};
        wayland_send(f->top->p, action==CSD_MOVE ? 5 : 6, a);
        __android_log_print(4,"AnvilDroidCsd","host %s serial=%u",action==CSD_MOVE?"move":"resize",serial);
      }
    } else {
      if (action && action==o->csd_pressed) {
        if (action==CSD_MENU) csd_menu_toggle(f);
        if (action==CSD_MINIMIZE) wayland_send(f->top->p,13,0);
        if (action==CSD_MAXIMIZE) {
          csd_toggle_maximize(f);
        }
        if (action==CSD_CLOSE && f->top->listener && !close_request_app(f->top->surface)) {
          closed=(void (*)(void *,struct proxy *))f->top->listener[1];
          closed_data=f->top->listener_data; closed_proxy=f->top->p;
        }
        if (action>=CSD_MINIMIZE) __android_log_print(4,"AnvilDroidCsd","host button action=%d",action);
      }
      if (f->top->surface && f->top->surface->resize.dragging) {
        resize_release(&f->top->surface->resize);
        __android_log_print(4, "AnvilDroidCsd", "resize release commit %dx%d",
                            f->top->surface->resize.width,
                            f->top->surface->resize.height);
      }
      o->csd_pressed=0;
    }
    csd_pointer_feedback(o);
  }
  pthread_mutex_unlock(mutex);
  if (closed) closed(closed_data,closed_proxy);
  if (!suppress) ((void (*)(void *,struct proxy *,uint32_t,uint32_t,uint32_t,uint32_t))o->listener[3])(
    o->listener_data,p,serial,time,button,state);
}
/* HWC binds wl_seat v5: keep the complete nine-event pointer ABI. */
static void csd_pointer_axis(void *d,struct proxy *p,uint32_t t,uint32_t axis,int32_t value) {
  struct object *o=d;
  if(o->menu_focus&&axis==0&&value) {
    pthread_mutex_lock(mutex);
    csd_menu_scroll(o,value>0?1:-1);
    pthread_mutex_unlock(mutex);
  }
  if (!o->csd_suppress) ((void (*)(void *,struct proxy *,uint32_t,uint32_t,int32_t))o->listener[4])(o->listener_data,p,t,axis,value);
}
static void csd_pointer_frame(void *d,struct proxy *p) {
  struct object *o=d; if (!o->csd_suppress && o->listener[5]) ((void (*)(void *,struct proxy *))o->listener[5])(o->listener_data,p);
}
static void csd_pointer_source(void *d,struct proxy *p,uint32_t source) {
  struct object *o=d; if (!o->csd_suppress && o->listener[6]) ((void (*)(void *,struct proxy *,uint32_t))o->listener[6])(o->listener_data,p,source);
}
static void csd_pointer_stop(void *d,struct proxy *p,uint32_t t,uint32_t axis) {
  struct object *o=d; if (!o->csd_suppress && o->listener[7]) ((void (*)(void *,struct proxy *,uint32_t,uint32_t))o->listener[7])(o->listener_data,p,t,axis);
}
static void csd_pointer_discrete(void *d,struct proxy *p,uint32_t axis,int32_t steps) {
  struct object *o=d; if (!o->csd_suppress && o->listener[8]) ((void (*)(void *,struct proxy *,uint32_t,int32_t))o->listener[8])(o->listener_data,p,axis,steps);
}
static void (*csd_pointer_listener[])(void) = {
  (void (*)(void))csd_pointer_enter,(void (*)(void))csd_pointer_leave,
  (void (*)(void))csd_pointer_motion,(void (*)(void))csd_pointer_button,
  (void (*)(void))csd_pointer_axis,(void (*)(void))csd_pointer_frame,
  (void (*)(void))csd_pointer_source,(void (*)(void))csd_pointer_stop,
  (void (*)(void))csd_pointer_discrete};
