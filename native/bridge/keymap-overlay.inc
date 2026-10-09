/* Same-surface editor. No injection: consume edit input, persist controls only.
 * Authorized requests are runtime-generation/task scoped and acknowledged only
 * after the Wayland event thread has attached the overlay. */
#ifndef ANVIL_KEYMAP_EDITOR
#define ANVIL_KEYMAP_EDITOR 0
#endif
#include "keymap-font.h"
#include "keymap-model.h"
extern int *__errno(void);
extern int *__errno_location(void);
#ifndef KEYMAP_BASE
#define KEYMAP_BASE "/data/waydroid_tmp/anvildroid"
#endif
struct keymap_overlay {
  struct csd_piece piece;
  struct keymap_model model;
  char nonce[33];
  char package[241];
  unsigned long token;
  int dirty, dragging, save_pending, close_pending, close_result, ack_pending,
      task;
  int panel_hidden, placement_hold;
  int passive;
  int sync_valid, sync_x, sync_y;
  struct proxy *sync_above;
  uint32_t active_keys[24];
  unsigned char flash_ticks[768];
};
static unsigned long keymap_token;
static struct keymap_overlay *keymap_paint_context;
static char keymap_closed_nonce[33];
static int keymap_closed_result;
static void keymap_wake(void);
static int keymap_number(const char *value, int limit) {
  if (!*value)
    return -1;
  int n = 0;
  for (; *value; ++value) {
    int digit = *value - '0';
    if (digit < 0 || digit > 9 || n > limit / 10 ||
        (n == limit / 10 && digit > limit % 10))
      return -1;
    n = n * 10 + digit;
  }
  return n;
}
static const char *keymap_label(int key, char *buf, size_t size) {
  static const int codes[] = {16, 17, 18, 19, 20, 21, 22, 23, 24,
                              25, 30, 31, 32, 33, 34, 35, 36, 37,
                              38, 44, 45, 46, 47, 48, 49, 50};
  static const char letters[] = "QWERTYUIOPASDFGHJKLZXCVBNM";
  for (unsigned i = 0; i < sizeof(codes) / sizeof(codes[0]); ++i)
    if (key == codes[i]) {
      buf[0] = letters[i];
      buf[1] = 0;
      return buf;
    }
  if (!key)
    return "KEY";
  if (key >= 2 && key <= 11) {
    buf[0] = key == 11 ? '0' : (char)('1' + key - 2);
    buf[1] = 0;
    return buf;
  }
  if (key >= 59 && key <= 68) {
    snprintf(buf, size, "F%d", key - 58);
    return buf;
  }
  switch (key) {
  case 57:
    return "SPACE";
  case 28:
    return "ENTER";
  case 15:
    return "TAB";
  case 14:
    return "BACK";
  case 42:
  case 54:
    return "SHIFT";
  case 29:
  case 97:
    return "CTRL";
  case 56:
  case 100:
    return "ALT";
  case 103:
    return "UP";
  case 108:
    return "DOWN";
  case 105:
    return "LEFT";
  case 106:
    return "RIGHT";
  case 87:
    return "F11";
  case 88:
    return "F12";
  default:
    snprintf(buf, size, "K%d", key);
    return buf;
  }
}
#include "keymap-editor-ui.h"
static void keymap_overlay_paint(uint32_t *pixels, int w, int h) {
  if(keymap_paint_context) {
    if(keymap_paint_context->passive)keymap_hints_paint(pixels,w,h,keymap_paint_context);
    else keymap_editor_paint(pixels,w,h,keymap_paint_context);
  }
}
static int keymap_load(int dir, const char *pkg, const char *runtime,
                       struct keymap_model *m) {
  int fd =
      openat(dir, pkg, PROFILE_NOFOLLOW | PROFILE_CLOEXEC | PROFILE_NONBLOCK);
  if (fd < 0)
    return PROFILE_ERRNO == 2 ? 0 : -1;
  char bytes[4096];
  long n = read(fd, bytes, sizeof(bytes) - 1);
  char extra;
  long tail = n >= 0 ? read(fd, &extra, 1) : -1;
  close(fd);
  if (n <= 0 || tail != 0)
    return -1;
  bytes[n] = 0;
  char r[129], p[241], version[16], count_text[16];
  int used = 0;
  if (sscanf(bytes, "ANVIL_KEYMAP %15s %128s %240s %15s%n", version, r, p,
             count_text, &used) != 4 ||
      !used || strcmp(version, "1") || strcmp(r, runtime) || strcmp(p, pkg))
    return -1;
  int count = keymap_number(count_text, KEYMAP_POINTS);
  if (count < 0)
    return -1;
  *m = (struct keymap_model){.count = count, .selected = -1};
  for (int i = 0; i < count; ++i) {
    int consumed = 0;
    struct keymap_point *q = &m->points[i];
    char values[4][16];
    if (sscanf(bytes + used, " %15s %15s %15s %15s%n", values[0], values[1],
               values[2], values[3], &consumed) != 4 ||
        !consumed)
      return -1;
    q->key = keymap_number(values[0], 767);
    q->nx = keymap_number(values[1], 65535);
    q->ny = keymap_number(values[2], 65535);
    q->hold = keymap_number(values[3], 1);
    used += consumed;
  }
  for (int i = used; i < n; ++i)
    if (bytes[i] != '\n' && bytes[i] != ' ' && bytes[i] != '\r' &&
        bytes[i] != '\t')
      return -1;
  return keymap_valid(m, 1) ? 1 : -1;
}
static int keymap_save(int dir, const char *pkg, const char *runtime,
                       const struct keymap_model *m) {
  if (!keymap_valid(m, 1))
    return 0;
  struct keymap_model prior;
  if (keymap_load(dir, pkg, runtime, &prior) < 0)
    return 0;
  char data[4096], temp[80];
  int used = snprintf(data, sizeof(data), "ANVIL_KEYMAP 1 %s %s %d\n", runtime,
                      pkg, m->count);
  for (int i = 0; i < m->count; ++i) {
    const struct keymap_point *p = &m->points[i];
    used += snprintf(data + used, sizeof(data) - (size_t)used, "%d %d %d %d\n",
                     p->key, p->nx, p->ny, p->hold);
  }
  uint64_t salt = 0;
  if (syscall(318, &salt, sizeof(salt), 0) != (long)sizeof(salt))
    return 0;
  snprintf(temp, sizeof(temp), ".pending-%d-%016llx", getpid(),
           (unsigned long long)salt);
  int fd = openat(dir, temp,
                  1 | PROFILE_CREAT | PROFILE_EXCL | PROFILE_NOFOLLOW |
                      PROFILE_CLOEXEC,
                  0600);
  if (fd < 0)
    return 0;
  int ok = write(fd, data, (unsigned long)used) == used && !fsync(fd);
  close(fd);
  if (ok)
    ok = !renameat(dir, temp, dir, pkg);
  if (ok)
    ok = !fsync(dir);
  if (!ok)
    unlinkat(dir, temp, 0);
  return ok;
}
static void keymap_reply(const char *nonce, const char *status) {
  if (!*nonce)
    return;
  int dir =
      open(KEYMAP_BASE, PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
  if (dir < 0)
    return;
  int fd = openat(
      dir, "keymap-reply.tmp",
      1 | PROFILE_CREAT | 01000 | PROFILE_NOFOLLOW | PROFILE_CLOEXEC, 0600);
  if (fd >= 0) {
    char data[160];
    int n = snprintf(data, sizeof(data), "%s %s\n", nonce, status);
    int ok = write(fd, data, (unsigned long)n) == n;
    close(fd);
    if (ok)
      renameat(dir, "keymap-reply.tmp", dir, "keymap-reply");
  }
  close(dir);
}
static void keymap_overlay_destroy(struct object *s) {
  if (!s || !s->overlay)
    return;
  if (s->overlay->nonce[0]) {
    snprintf(keymap_closed_nonce, sizeof(keymap_closed_nonce), "%s",
             s->overlay->nonce);
    keymap_closed_result =
        s->overlay->close_result ? s->overlay->close_result : 3;
  }
  for (struct object *o = objects; o; o = o->next)
    if (o->overlay_focus == s) {
      o->overlay_focus = 0;
      o->overlay_pressed = 0;
      o->csd_suppress = 1;
    }
  struct csd_piece *p = &s->overlay->piece;
  if (p->subsurface) {
    wayland_send(p->subsurface, 0, 0);
    real_destroy(p->subsurface);
  }
  if (p->surface) {
    wayland_send(p->surface, 0, 0);
    real_destroy(p->surface);
  }
  __android_log_print(4, "AnvilKeymapOverlay",
                      "editor removed package=%s task=%d result=%d",
                      s->overlay->package, s->overlay->task,
                      s->overlay->close_result);
  free(s->overlay);
  s->overlay = 0;
}
static void keymap_hints_destroy(struct object *s) {
  if(!s)return;
  s->hints_probe=0;s->hints_task=0;
  if(!s->hints)return;
  scrcpy_mapping_cancel();
  struct csd_piece *p=&s->hints->piece;
  if(p->subsurface){wayland_send(p->subsurface,0,0);real_destroy(p->subsurface);}
  if(p->surface){wayland_send(p->surface,0,0);real_destroy(p->surface);}
  free(s->hints);s->hints=0;
}
static int keymap_task_owner(struct object *s) {
  if(!s->app||!s->app_id||!s->has_bounds)return 0;
  int id=0,count=0,surfaces=0;
  for(int i=0;i<task_count;++i)if(!strcmp(tasks[i].package,s->app_id)) {
    id=tasks[i].id;++count;
  }
  for(struct object *o=objects;o;o=o->next)
    if(o->app&&o->app_id&&!strcmp(o->app_id,s->app_id))++surfaces;
  return count==1&&surfaces==1?id:0;
}
static void keymap_hints_clear(struct object *s) {
  if(!s||!s->hints)return;
  scrcpy_mapping_cancel();
  for(int i=0;i<24;++i)s->hints->active_keys[i]=0;
  memset(s->hints->flash_ticks,0,sizeof(s->hints->flash_ticks));
  s->hints->dirty=1;
  keymap_wake();
}
static void keymap_connection_lost(void) {
  for (struct object *s = objects; s; s = s->next)
  {
    keymap_hints_destroy(s);
    if (s->overlay) {
      s->overlay->close_result = 4;
      keymap_overlay_destroy(s);
    }
  }
}
static void keymap_overlay_sync(struct object *s, int x, int y, int w, int h) {
  if (!s)
    return;
#ifdef ANVIL_KEYMAP_OVERLAY_PROBE
  if (!s->overlay && !s->overlay_dismissed && s->app_id &&
      !strcmp(s->app_id, "com.android.settings")) {
    s->overlay = calloc(1, sizeof(*s->overlay));
    if (s->overlay) {
      s->overlay->model.selected = -1;
      s->overlay->dirty = 1;
      snprintf(s->overlay->package, sizeof(s->overlay->package), "%s",
               s->app_id);
    }
  }
#endif
  if(s->hints&&(s->hints->close_pending||keymap_task_owner(s)!=s->hints->task))
    keymap_hints_destroy(s);
  if (s->overlay && s->overlay->close_pending) {
    s->overlay_dismissed = 1;
    keymap_overlay_destroy(s);
  }
  if(s->overlay&&s->hints&&s->hints->piece.surface) {
    union argument detach[3]={{.o=0},{.i=0},{.i=0}};
    wayland_send(s->hints->piece.surface,1,detach);
    wayland_send(s->hints->piece.surface,6,0);
    s->hints->dirty=1;
  }
  struct keymap_overlay *f=s->overlay?s->overlay:s->hints;
  if(!f)return;
  struct csd_piece *p=&f->piece;
  if (!csd_compositor || !csd_subcompositor || !csd_shm)
    return;
  if (w < 300 || h < 120 || w > 8192 || h > 8192 || (int64_t)w * h > 16777216) {
    f->ack_pending = 4;
    f->close_result = 4;
    f->close_pending = 1;
    return;
  }
  if (!p->surface) {
    union argument a = {.o = 0};
    p->surface = real_marshal(csd_compositor, 0, &a, csd_surface_i, 1);
    if (!p->surface) {
      f->ack_pending = 4;
      f->close_result = 4;
      f->close_pending = 1;
      return;
    }
    /* An empty region, never NULL (which means infinite input), is mandatory
     * before a passive surface can attach any buffer. Fail closed if allocation
     * fails: no hint surface may become a pointer/touch target. */
    if(f->passive&&!csd_input(p->surface,0,0,0,0)) {
      wayland_send(p->surface,0,0);real_destroy(p->surface);p->surface=0;
      f->close_pending=1;return;
    }
    union argument sub[3] = {{.o = 0}, {.o = p->surface}, {.o = s->p}};
    p->subsurface =
        real_marshal(csd_subcompositor, 1, sub, csd_subsurface_i, 1);
    if (!p->subsurface) {
      f->ack_pending = 4;
      f->close_result = 4;
      f->close_pending = 1;
      return;
    }
  }
  struct proxy *above_surface=0;
  for (struct object *sub = objects; sub; sub = sub->next)
    if (sub->parent == s && sub->surface &&
        !strcmp(sub->p->interface->name, "wl_subsurface")) {
      above_surface=sub->surface->p;
      break;
    }
  if(f->passive&&f->sync_valid&&!f->dirty&&p->width==w&&p->height==h&&
     f->sync_x==x&&f->sync_y==y&&f->sync_above==above_surface)return;
  union argument pos[2] = {{.i = x}, {.i = y}};
  wayland_send(p->subsurface, 1, pos);
  if(above_surface){union argument above={.o=above_surface};wayland_send(p->subsurface,2,&above);}
  if (f->dirty || p->width != w || p->height != h) {
    if (p->width != w || p->height != h)
      f->dragging = 0;
    keymap_paint_context = f;
    struct proxy *buffer = csd_paint(4, w, h, 0);
    keymap_paint_context = 0;
    if (!buffer) {
      f->ack_pending = 4;
      f->close_result = 4;
      f->close_pending = 1;
      return;
    }
    union argument attach[3] = {{.o = buffer}, {.i = 0}, {.i = 0}};
    wayland_send(p->surface, 1, attach);
    union argument damage[4] = {{.i = 0}, {.i = 0}, {.i = w}, {.i = h}};
    wayland_send(p->surface, 2, damage);
    if(!f->passive)csd_input(p->surface, 0, 0, w, h);
    p->width = w;
    p->height = h;
    f->dirty = 0;
    if (!f->passive && !f->ack_pending)
      f->ack_pending = 1;
    __android_log_print(4, "AnvilKeymapOverlay",
                        "attached %s origin=%d,%d size=%dx%d", s->app_id, x, y,
                        w, h);
  }
  wayland_send(p->surface, 6, 0);
  f->sync_valid=1;f->sync_x=x;f->sync_y=y;f->sync_above=above_surface;
}
static int keymap_overlay_enter(struct object *pointer, struct proxy *surface) {
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay && s->overlay->piece.surface == surface) {
      pointer->overlay_focus = s;
      return 1;
    }
  return 0;
}
static void keymap_overlay_motion(struct object *pointer) {
  struct object *s = pointer->overlay_focus;
  if (!s || !s->overlay)
    return;
  struct keymap_overlay *f = s->overlay;
  struct keymap_layout l=keymap_layout(f->piece.width,f->piece.height,f->panel_hidden);
  if (f->dragging && !f->save_pending &&
      !keymap_ui_hit(l,pointer->csd_x,pointer->csd_y)) {
    keymap_move(&f->model, f->model.selected, pointer->csd_x, pointer->csd_y,
                f->piece.width, f->piece.height);
    f->dirty = 1;
    keymap_wake();
  }
}
static void keymap_overlay_pointer_cancel(struct object *pointer) {
  if (pointer->overlay_focus && pointer->overlay_focus->overlay)
    pointer->overlay_focus->overlay->dragging = 0;
  pointer->overlay_pressed = 0;
}
static void keymap_overlay_button(struct object *pointer, uint32_t button,
                                  uint32_t state) {
  struct object *s = pointer->overlay_focus;
  if (!s || !s->overlay || button != 0x110)
    return;
  struct keymap_overlay *f = s->overlay;
  struct keymap_model *m = &f->model;
  int x = pointer->csd_x, y = pointer->csd_y;
  struct keymap_layout l=keymap_layout(f->piece.width,f->piece.height,f->panel_hidden);
  int hit=keymap_ui_hit(l,x,y);
  if(!hit)hit=keymap_point_hit(m,f->piece.width,f->piece.height,x,y);
  if(!hit&&!f->panel_hidden&&keymap_contains((struct keymap_rect){12,12,
      f->piece.width<360?f->piece.width-88:236,48},x,y))hit=-1;
  if (state) {
    pointer->overlay_pressed = hit;
    if ((hit==0||hit>=200) && !f->save_pending) {
      int found=hit>=200?hit-200:-1;
      if (m->placing && !hit) {
        if(keymap_add(m,x,y,f->piece.width,f->piece.height))
          m->points[m->selected].hold=f->placement_hold;
      }
      else
        m->selected = found;
      f->dragging = m->selected >= 0;
      f->dirty = 1;
      keymap_wake();
    }
  } else {
    f->dragging = 0;
    if (hit && hit == pointer->overlay_pressed && !f->save_pending) {
      if (hit == 1 && m->count<KEYMAP_POINTS) {
        m->placing = 1;
        m->selected = -1;
        m->error = 0;
      }
      if (hit == 2) {
        if (keymap_valid(m, 1))
          f->save_pending = 1;
        else
          m->error = 2;
      }
      if (hit == 3)
        keymap_remove(m);
      if(hit>=100&&hit<100+KEYMAP_POINTS) {
        m->selected=hit-100;keymap_remove(m);
      }
      if (hit == 4 || hit == 7 || hit == 8) {
        int selected=m->selected>=0&&m->selected<m->count;
        int previous=selected?m->points[m->selected].hold:f->placement_hold;
        f->placement_hold=hit==4?!previous:hit==8;
        if(selected) {m->points[m->selected].hold=f->placement_hold;m->dirty=1;}
      }
      if(hit==6)f->panel_hidden=!f->panel_hidden;
      if (hit == 5) {
        f->ack_pending = 3;
        f->close_pending = 1;
        s->overlay_dismissed = 1;
      }
      f->dirty = 1;
      keymap_wake();
    }
    pointer->overlay_pressed = 0;
  }
}
static void keymap_done(void *unused, struct proxy *callback, uint32_t serial) {
  (void)serial;
  pthread_mutex_lock(mutex);
  if ((uint64_t)(uintptr_t)unused != keymap_display_epoch ||
      callback != keymap_callback || !keymap_connection_ready()) {
    pthread_mutex_unlock(mutex);
    return;
  }
  keymap_callback = 0;
  keymap_scheduled = 0;
  real_destroy(callback);
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay || s->hints) {
      keymap_overlay_sync(s, s->bx, s->by, s->bw, s->bh);
      wayland_send(s->p, 6, 0);
    }
  ime_sync();
  /* A static Android app may not submit another frame to flush this repaint. */
  if (real_display_flush(keymap_display) < 0 && PROFILE_ERRNO != 11) {
    keymap_display_blocked = 1;
    keymap_connection_lost();
  }
  pthread_mutex_unlock(mutex);
}
static void (*keymap_done_listener[])(void) = {(void (*)(void))keymap_done};
static void keymap_wake(void) {
  if (!keymap_connection_ready() || keymap_scheduled)
    return;
  union argument a = {.o = 0};
  struct proxy *cb =
      real_marshal(keymap_sync_proxy, 0, &a,
                   keymap_sync_proxy->interface->methods[0].types[0], 1);
  if (!cb)
    return;
  keymap_scheduled = 1;
  keymap_callback = cb;
  if (real_listen(cb, keymap_done_listener,
                  (void *)(uintptr_t)keymap_display_epoch)) {
    keymap_cancel_callback();
    return;
  }
  // Only the genuine connection pointer is valid for display APIs. The owned
  // wrapper above is used exclusively for proxy request/queue operations.
  if (real_display_flush(keymap_display) < 0 && PROFILE_ERRNO != 11) {
    keymap_cancel_callback();
    keymap_display_blocked = 1;
    keymap_connection_lost();
  }
}
static struct object *keymap_owner(struct proxy *p) {
  struct object *s = find(p);
  if (!s)
    return 0;
  if (s && s->app)
    return s;
  for (struct object *o = objects; o; o = o->next)
    if (o->surface == s && o->parent && o->parent->app &&
        !strcmp(o->p->interface->name, "wl_subsurface"))
      return o->parent;
  return 0;
}
static void keymap_kmap(void *data, struct proxy *p, uint32_t format,
                        int32_t fd, uint32_t size) {
  struct object *o = data;
  ((void (*)(void *, struct proxy *, uint32_t, int32_t,
             uint32_t))o->listener[0])(o->listener_data, p, format, fd, size);
}
static void keymap_kenter(void *data, struct proxy *p, uint32_t serial,
                          struct proxy *surface, void *keys) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  keymap_hints_clear(o->keyboard_focus);
  o->keyboard_focus = keymap_owner(surface);
  pthread_mutex_unlock(mutex);
  ((void (*)(void *, struct proxy *, uint32_t, struct proxy *,
             void *))o->listener[1])(o->listener_data, p, serial, surface,
                                     keys);
}
static void keymap_kleave(void *data, struct proxy *p, uint32_t serial,
                          struct proxy *surface) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  keymap_hints_clear(o->keyboard_focus);
  o->keyboard_focus = 0;
  for (int i = 0; i < 24; ++i)
    o->keymap_blocked_keys[i] = 0;
  pthread_mutex_unlock(mutex);
  ((void (*)(void *, struct proxy *, uint32_t, struct proxy *))o->listener[2])(
      o->listener_data, p, serial, surface);
}
static void keymap_key(void *data, struct proxy *p, uint32_t serial,
                       uint32_t time, uint32_t key, uint32_t state) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  struct keymap_overlay *f = o->keyboard_focus ? o->keyboard_focus->overlay : 0;
  int blocked =
      key < 768 && (o->keymap_blocked_keys[key / 32] & (1u << (key % 32)));
  if (f && key < 768) {
    if (state)
      o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
  }
  if (!state && key < 768)
    o->keymap_blocked_keys[key / 32] &= ~(1u << (key % 32));
  if (f && state && !f->save_pending) {
    if (key == 1) {
      f->close_pending = 1;
      f->ack_pending = 3;
    } else
      keymap_assign(&f->model, (int)key);
    f->dirty = 1;
    keymap_wake();
  }
  int suppress = f != 0 || blocked;
  if (state && o->keyboard_focus && !f && !blocked) {
    struct object *s = o->keyboard_focus;
    if (key == 87) { /* Wayland uses evdev KEY_F11, not XKB keycode 95. */
      struct proxy *top = csd_toplevel(s);
      if (top) {
        int target = !s->host_fullscreen;
        wayland_send(top, target ? 11 : 12, 0);
        s->host_fullscreen = target;
        o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
        suppress = 1;
      }
    } else if (key == 1 && s->host_fullscreen) { /* Esc exits host fullscreen */
      struct proxy *top = csd_toplevel(s);
      if (top) {
        wayland_send(top, 12, 0);
        s->host_fullscreen = 0;
        o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
        suppress = 1;
      }
    }
  }
  struct keymap_overlay *hints=o->keyboard_focus?o->keyboard_focus->hints:0;
  if(!suppress&&hints&&key<768) {
    if (scrcpy_mapping_key(o->keyboard_focus, hints, (int)key, state))
      suppress = 1;
    for(int i=0;i<hints->model.count;++i)if(hints->model.points[i].key==(int)key) {
      uint32_t bit=1u<<(key%32);
      if(state) {
        hints->active_keys[key/32]|=bit;
        /* Four 50ms poll ticks keep a quick tap visible for 150-200ms. */
        hints->flash_ticks[key]=4;
      }
      else hints->active_keys[key/32]&=~bit;
      hints->dirty=1;keymap_wake();break;
    }
  }
  pthread_mutex_unlock(mutex);
  if (!suppress)
    ((void (*)(void *, struct proxy *, uint32_t, uint32_t, uint32_t,
               uint32_t))o->listener[3])(o->listener_data, p, serial, time, key,
                                         state);
}
static void keymap_mods(void *data, struct proxy *p, uint32_t serial,
                        uint32_t depressed, uint32_t latched, uint32_t locked,
                        uint32_t group) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  int blocked = o->keyboard_focus && o->keyboard_focus->overlay;
  pthread_mutex_unlock(mutex);
  if (!blocked)
    ((void (*)(void *, struct proxy *, uint32_t, uint32_t, uint32_t, uint32_t,
               uint32_t))o->listener[4])(o->listener_data, p, serial, depressed,
                                         latched, locked, group);
}
static void keymap_repeat(void *data, struct proxy *p, int32_t rate,
                          int32_t delay) {
  struct object *o = data;
  if (o->listener[5])
    ((void (*)(void *, struct proxy *, int32_t, int32_t))o->listener[5])(
        o->listener_data, p, rate, delay);
}
static void (*keymap_keyboard_listener[])(void) = {
    (void (*)(void))keymap_kmap,   (void (*)(void))keymap_kenter,
    (void (*)(void))keymap_kleave, (void (*)(void))keymap_key,
    (void (*)(void))keymap_mods,   (void (*)(void))keymap_repeat};
static int keymap_touch_slot(struct object *o, int32_t id) {
  for (int i = 0; i < 32; ++i)
    if ((o->keymap_touch_present & (1u << i)) && o->keymap_touch_ids[i] == id)
      return i;
  return -1;
}
static void keymap_tdown(void *data, struct proxy *p, uint32_t serial,
                         uint32_t time, struct proxy *surface, int32_t id,
                         int32_t x, int32_t y) {
  struct object *o = data;
  int slot = -1, blocked = 0;
  pthread_mutex_lock(mutex);
  for (int i = 0; i < 32; ++i)
    if (!(o->keymap_touch_present & (1u << i))) {
      slot = i;
      break;
    }
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay && s->overlay->piece.surface == surface)
      blocked = 1;
  if(game_fit_bar(surface))blocked=1;
  if (slot >= 0) {
    o->keymap_touch_present |= 1u << slot;
    o->keymap_touch_ids[slot] = id;
    o->touch_surfaces[slot]=surface;
    if (blocked)
      o->keymap_touch_blocked |= 1u << slot;
    else
      o->keymap_touch_blocked &= ~(1u << slot);
  }
  x=full_ui_input(surface,x,0);y=full_ui_input(surface,y,1);
  x=game_fit_input(surface,x,0);y=game_fit_input(surface,y,1);
  pthread_mutex_unlock(mutex);
  if (slot >= 0 && !blocked)
    ((void (*)(void *, struct proxy *, uint32_t, uint32_t, struct proxy *,
               int32_t, int32_t, int32_t))o->listener[0])(
        o->listener_data, p, serial, time, surface, id, x, y);
}
static void keymap_tup(void *data, struct proxy *p, uint32_t serial,
                       uint32_t time, int32_t id) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  int slot = keymap_touch_slot(o, id), blocked = 1;
  if (slot >= 0) {
    blocked = (o->keymap_touch_blocked & (1u << slot)) != 0;
    o->keymap_touch_present &= ~(1u << slot);
    o->keymap_touch_blocked &= ~(1u << slot);
    o->touch_surfaces[slot]=0;
  }
  pthread_mutex_unlock(mutex);
  if (!blocked)
    ((void (*)(void *, struct proxy *, uint32_t, uint32_t,
               int32_t))o->listener[1])(o->listener_data, p, serial, time, id);
}
static void keymap_tmotion(void *data, struct proxy *p, uint32_t time,
                           int32_t id, int32_t x, int32_t y) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  int slot = keymap_touch_slot(o, id),
      blocked = slot < 0 || (o->keymap_touch_blocked & (1u << slot));
  if(slot>=0){x=full_ui_input(o->touch_surfaces[slot],x,0);y=full_ui_input(o->touch_surfaces[slot],y,1);}
  if(slot>=0){x=game_fit_input(o->touch_surfaces[slot],x,0);y=game_fit_input(o->touch_surfaces[slot],y,1);}
  pthread_mutex_unlock(mutex);
  if (!blocked)
    ((void (*)(void *, struct proxy *, uint32_t, int32_t, int32_t,
               int32_t))o->listener[2])(o->listener_data, p, time, id, x, y);
}
static void keymap_tframe(void *data, struct proxy *p) {
  struct object *o = data;
  ((void (*)(void *, struct proxy *))o->listener[3])(o->listener_data, p);
}
static void keymap_tcancel(void *data, struct proxy *p) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  int normal = (o->keymap_touch_present & ~o->keymap_touch_blocked) != 0;
  o->keymap_touch_present = o->keymap_touch_blocked = 0;
  pthread_mutex_unlock(mutex);
  if (normal)
    ((void (*)(void *, struct proxy *))o->listener[4])(o->listener_data, p);
}
static void (*keymap_touch_listener[])(void) = {
    (void (*)(void))keymap_tdown, (void (*)(void))keymap_tup,
    (void (*)(void))keymap_tmotion, (void (*)(void))keymap_tframe,
    (void (*)(void))keymap_tcancel};

/* Called under the bridge mutex; only model state, no Wayland work here. */
static void keymap_hints_set(struct object *s,const struct keymap_model *model,int task) {
  s->hints_task=task;
  if(!s->hints&&model->count)s->hints=calloc(1,sizeof(*s->hints));
  if(!s->hints)return;
  struct keymap_overlay *f=s->hints;
  f->passive=1;f->model=*model;f->model.selected=-1;
  f->task=task;f->dirty=1;f->close_pending=!model->count;
  snprintf(f->package,sizeof(f->package),"%s",s->app_id);
  for(int i=0;i<24;++i)f->active_keys[i]=0;
  memset(f->flash_ticks,0,sizeof(f->flash_ticks));
  keymap_wake();
}
/* One profile read per poll, outside the event-thread lock. Bind completion
 * to a never-reused token + current task/package, not a borrowed object pointer. */
static void keymap_hints_poll(const char *runtime) {
  char pkg[241]="";unsigned long token=0;int task=0;
  pthread_mutex_lock(mutex);
  for(struct object *s=objects;s;s=s->next)if(s->hints) {
    struct keymap_overlay *f=s->hints;
    for(int i=0;i<f->model.count;++i) {
      int key=f->model.points[i].key;
      if(key>0&&key<768&&f->flash_ticks[key]&&!--f->flash_ticks[key]&&
         !(f->active_keys[key/32]&(1u<<(key%32)))) {
        f->dirty=1;keymap_wake();
      }
    }
  }
  if(keymap_connection_ready())for(struct object *s=objects;s;s=s->next) {
    int owner=keymap_task_owner(s);
    if(s->hints&&owner!=s->hints->task) {
      s->hints->close_pending=1;keymap_wake();
    }
    if(!token&&owner&&!s->overlay&&!s->hints&&s->hints_task!=owner&&
       profile_name(s->app_id,1)) {
      s->hints_task=owner;token=s->hints_probe=++keymap_token;task=owner;
      snprintf(pkg,sizeof(pkg),"%s",s->app_id);
    }
  }
  pthread_mutex_unlock(mutex);
  if(!token)return;
  struct keymap_model model={.selected=-1};
  int dir=open(KEYMAP_BASE "/keymaps",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  int loaded=dir>=0?keymap_load(dir,pkg,runtime,&model):-1;
  if(dir>=0)close(dir);
  pthread_mutex_lock(mutex);
  for(struct object *s=objects;s;s=s->next)
    if(s->hints_probe==token&&s->app_id&&!strcmp(s->app_id,pkg)&&
       !s->overlay&&keymap_task_owner(s)==task) {
      if(loaded==1)keymap_hints_set(s,&model,task);
      break;
    }
  pthread_mutex_unlock(mutex);
}
static void keymap_io_poll(void) {
  if (!ANVIL_KEYMAP_EDITOR)
    return;
  const char *runtime = getenv("ANVILDROID_RUNTIME_ID"),
             *generation = getenv("ANVILDROID_RUNTIME_GENERATION");
  if (!profile_name(runtime, 0) || !generation)
    return;
  keymap_hints_poll(runtime);
  char closed_nonce[33] = "";
  int closed_result = 0;
  pthread_mutex_lock(mutex);
  if (keymap_closed_nonce[0]) {
    snprintf(closed_nonce, sizeof(closed_nonce), "%s", keymap_closed_nonce);
    closed_result = keymap_closed_result;
    keymap_closed_nonce[0] = 0;
  }
  pthread_mutex_unlock(mutex);
  if (closed_result)
    keymap_reply(closed_nonce, closed_result == 2   ? "saved"
                               : closed_result == 4 ? "error"
                                                    : "cancelled");
  char nonce[33] = "", pkg[241] = "", gen[33], command[512];
  int task = 0, version = 0, used = 0;
  int base =
      open(KEYMAP_BASE, PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
  if (base < 0)
    return;
  int fd = openat(base, "keymap-request",
                  PROFILE_NOFOLLOW | PROFILE_CLOEXEC | PROFILE_NONBLOCK);
  if (fd >= 0) {
    long n = read(fd, command, sizeof(command) - 1);
    close(fd);
    unlinkat(base, "keymap-request", 0);
    if (n > 0) {
      command[n] = 0;
      if (sscanf(command, "%d %32s %32s %d %240s %n", &version, nonce, gen,
                 &task, pkg, &used) != 5 ||
          command[used] || version != 1 || strcmp(gen, generation) ||
          !profile_name(pkg, 1) || task <= 0)
        nonce[0] = 0;
    }
  }
  close(base);
  if (*nonce) {
    struct keymap_model model = {.selected = -1};
    mkdir(KEYMAP_BASE "/keymaps", 0700);
    int dir = open(KEYMAP_BASE "/keymaps",
                   PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
    int valid = dir >= 0 ? keymap_load(dir, pkg, runtime, &model) : -1;
    if (dir >= 0)
      close(dir);
    pthread_mutex_lock(mutex);
    struct object *target = 0;
    int matches = 0, busy = 0;
    for (struct object *s = objects; s; s = s->next) {
      if (s->keymap_touch_present)
        busy = 1;
      if (s->p && s->listener &&
          (!strcmp(s->p->interface->name, "wl_keyboard") ||
           !strcmp(s->p->interface->name, "wl_pointer") ||
           !strcmp(s->p->interface->name, "wl_touch")) &&
          get_version(s->p) > 5)
        busy = 1;
      if (s->overlay)
        busy = 1;
      if (s->app && s->app_id && !strcmp(s->app_id, pkg) && s->has_bounds) {
        target = s;
        ++matches;
      }
    }
    int tasks_match = 0;
    for (int i = 0; i < task_count; ++i)
      if (!strcmp(tasks[i].package, pkg)) {
        ++tasks_match;
        if (tasks[i].id != task)
          tasks_match += 128;
      }
    int accepted = keymap_connection_ready() && valid >= 0 && !busy &&
                   matches == 1 && tasks_match == 1 && target->bw >= 300 &&
                   target->bh >= 120 && !target->resize.dragging;
    if (accepted) {
      target->overlay = calloc(1, sizeof(*target->overlay));
      accepted = target->overlay != 0;
      if (accepted) {
        struct keymap_overlay *f = target->overlay;
        f->model = model;
        f->model.selected = -1;
        f->task = task;
        snprintf(f->package, sizeof(f->package), "%s", pkg);
        f->token = ++keymap_token;
        keymap_hints_clear(target);
        f->dirty = 1;
        snprintf(f->nonce, sizeof(f->nonce), "%s", nonce);
        keymap_wake();
      }
    }
    __android_log_print(4, "AnvilKeymapOverlay",
                        "request %s task=%d accepted=%d surfaces=%d tasks=%d "
                        "busy=%d profile=%d",
                        pkg, task, accepted, matches, tasks_match, busy, valid);
    pthread_mutex_unlock(mutex);
    if (!accepted)
      keymap_reply(nonce, "error");
  }
  /* Snapshot one rare save/ack, then do file IO without blocking Wayland. */
  struct keymap_model model;
  unsigned long token = 0;
  int saving = 0, ack = 0;
  pthread_mutex_lock(mutex);
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay) {
      struct keymap_overlay *f = s->overlay;
      if (f->token && !f->close_pending) {
        int matching = 0, owner = 0;
        for (int i = 0; i < task_count; ++i)
          if (!strcmp(tasks[i].package, f->package)) {
            ++matching;
            if (tasks[i].id == f->task)
              owner = 1;
          }
        if (matching != 1 || !owner || !s->app_id ||
            strcmp(s->app_id, f->package)) {
          f->close_pending = 1;
          f->close_result = 3;
          f->save_pending = 0;
          f->ack_pending = 3;
          keymap_wake();
        }
      }
      if (f->save_pending == 1 || f->ack_pending > 0) {
        token = f->token;
        saving = f->save_pending == 1;
        ack = f->ack_pending;
        if (saving) {
          f->save_pending = 2;
          model = f->model;
        }
        f->ack_pending = -1;
        if (f->close_pending)
          keymap_wake();
        snprintf(nonce, sizeof(nonce), "%s", f->nonce);
        snprintf(pkg, sizeof(pkg), "%s", f->package);
        break;
      }
    }
  pthread_mutex_unlock(mutex);
  if (!token)
    return;
  if (saving) {
    int dir = open(KEYMAP_BASE "/keymaps",
                   PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
    int ok = dir >= 0 && keymap_save(dir, pkg, runtime, &model);
    if (dir >= 0)
      close(dir);
    pthread_mutex_lock(mutex);
    for (struct object *s = objects; s; s = s->next)
      if (s->overlay && s->overlay->token == token) {
        struct keymap_overlay *f = s->overlay;
        f->save_pending = 0;
        f->dirty = 1;
        if (ok) {
          keymap_hints_set(s,&model,f->task);
          f->close_pending = 1;
          f->close_result = 2;
        } else
          f->model.error = 3;
        keymap_wake();
        break;
      }
    pthread_mutex_unlock(mutex);
    /* Saving storage is not the end of the edit session: the event thread
     * still has to remove the input-consuming surface. Publish success via
     * keymap_closed_nonce only after keymap_overlay_destroy has run. */
    if (!ok)
      keymap_reply(nonce, "error");
  } else
    keymap_reply(nonce, ack == 1   ? "editing"
                        : ack == 2 ? "saved"
                        : ack == 3 ? "cancelled"
                                   : "error");
}
