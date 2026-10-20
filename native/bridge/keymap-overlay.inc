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
  struct {struct csd_piece piece;struct proxy *xdg,*top,*decoration;
    int configured,width,height,dock_pending,dirty,decoration_mode,decoration_pending;} panel;
  int detached,panel_paint;
  struct keymap_model model;
  char nonce[33];
  char package[241];
  unsigned long token;
  int dirty, dragging, save_pending, close_pending, close_result, ack_pending,
      task;
  int panel_hidden, placement_hold;
  int property_page, binding_capture, placement_kind, capture_status;
  int type_open, inspector_scroll, palette_scroll, capture_field;
  int testing, placement_release, baseline_valid;
  int numeric_capture, numeric_field, numeric_value, numeric_digits;
  int history_count, history_index;
  struct keymap_model baseline, *history;
  long last_click_ms;
  int last_click_index, press_x, press_y, drag_moved, deferred_binding;
  int passive;
  int disabled, hints_hidden;
  int sync_valid, sync_x, sync_y;
  struct proxy *sync_above;
  uint32_t active_keys[24];
  unsigned char flash_ticks[768];
};
static unsigned long keymap_token;
static struct keymap_overlay *keymap_paint_context;
static char keymap_closed_nonce[33];
static int keymap_closed_result;
static char keymap_shortcut_package[241];
static int keymap_shortcut_task;
static void keymap_wake(void);
static long map_monotonic_ms(void);
static int keymap_marker_clear;
static int keymap_scheme_lock(const char *);
static void keymap_scheme_unlock(void);
static int keymap_panel_header(const struct keymap_overlay *);
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
static const char *keymap_point_label(const struct keymap_point *q,char *buf,size_t size) {
  if(q->kind==KEYMAP_DPAD)return q->up_key&&q->up_key!=17?"MOVE":"WASD";
  if(q->source==KEYMAP_SOURCE_POINTER)return "MOUSE";
  if(q->source==KEYMAP_SOURCE_MOUSE){snprintf(buf,size,"M%d",q->key-271);return buf;}
  return keymap_label(q->key,buf,size);
}
#include "keymap-editor-ui.h"
#include "keymap-detached.inc"
static void keymap_overlay_paint(uint32_t *pixels, int w, int h) {
  if(keymap_paint_context) {
    if(keymap_paint_context->panel_paint)keymap_panel_paint(pixels,w,h,keymap_paint_context);
    else if(keymap_paint_context->passive)keymap_hints_paint(pixels,w,h,keymap_paint_context);
    else keymap_editor_paint(pixels,w,h,keymap_paint_context);
  }
}
#include "keymap-schema.h"
static int keymap_load(int dir, const char *pkg, const char *runtime,
                       struct keymap_model *m) {
  int fd =
      openat(dir, pkg, PROFILE_NOFOLLOW | PROFILE_CLOEXEC | PROFILE_NONBLOCK);
  if (fd < 0)
    return PROFILE_ERRNO == 2 ? 0 : -1;
  char bytes[KEYMAP_PROFILE_LIMIT+1];
  long n = read(fd, bytes, sizeof(bytes) - 1);
  char extra;
  long tail = n >= 0 ? read(fd, &extra, 1) : -1;
  close(fd);
  if (n <= 0 || tail != 0)
    return -1;
  bytes[n] = 0;
  return keymap_profile_parse(bytes,(int)n,pkg,runtime,m)?1:-1;
}
static int keymap_save(int dir, const char *pkg, const char *runtime,
                       const struct keymap_model *m) {
  if (!keymap_valid(m, 1))
    return 0;
  struct keymap_model prior;
  if (keymap_load(dir, pkg, runtime, &prior) < 0)
    return 0;
  char data[KEYMAP_PROFILE_LIMIT+1], temp[80];
  int used=keymap_profile_format(data,sizeof(data),pkg,runtime,m);
  if(!used)return 0;
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
  keymap_panel_destroy(s->overlay);
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
  scrcpy_mapping_cancel();
  free(s->overlay->history);
  keymap_marker_clear=1;
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
/* A full Android canvas has no package app_id. Only select an unambiguous
 * visible app task. The launcher is the fallback when no app is visible. */
static const struct task *keymap_canvas_task(struct object *s) {
  if(!s||!s->full_ui)return 0;
  const struct task *target=0,*launcher=0;
  for(int i=0;i<task_count;++i) {
    const struct task *t=&tasks[i];
    if(!strcmp(t->package,"com.android.systemui")||!strcmp(t->package,"android"))continue;
    if(!strcmp(t->package,"com.android.launcher3")) {
      if(launcher)return 0;
      launcher=t;continue;
    }
    if(target)return 0;
    if(t->id<=0||t->x<0||t->y<0||t->width<2||t->height<2||
       t->x>s->full_canvas_w-t->width||t->y>s->full_canvas_h-t->height)return 0;
    target=t;
  }
  if(!target)target=launcher;
  if(target&&(target->id<=0||target->x<0||target->y<0||target->width<2||target->height<2||
       target->x>s->full_canvas_w-target->width||target->y>s->full_canvas_h-target->height))return 0;
  return target;
}
static const char *keymap_package(struct object *s) {
  if(!s)return 0;
  if(!s->full_ui)return s->app_id;
  const struct task *t=keymap_canvas_task(s);
  return t?t->package:0;
}
static int keymap_task_owner(struct object *s) {
  if(s&&s->full_ui) {
    const struct task *t=keymap_canvas_task(s);return t?t->id:0;
  }
  if(!s||!s->app||!s->app_id||!s->has_bounds)return 0;
  int id=0,count=0,surfaces=0;
  for(int i=0;i<task_count;++i)if(!strcmp(tasks[i].package,s->app_id)) {
    id=tasks[i].id;++count;
  }
  for(struct object *o=objects;o;o=o->next)
    if(o->app&&o->app_id&&!strcmp(o->app_id,s->app_id))++surfaces;
  return count==1&&surfaces==1?id:0;
}
static int full_ui_scaled(struct object *,int,int,int);
static void keymap_canvas_bounds(struct object *s) {
  const struct task *t=keymap_canvas_task(s);
  if(!t){s->has_bounds=0;return;}
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  s->bx=ox+full_ui_scaled(s,t->x,0,0);
  s->by=oy+full_ui_scaled(s,t->y,1,0);
  s->bw=full_ui_scaled(s,t->x+t->width,0,1)-(s->bx-ox);
  s->bh=full_ui_scaled(s,t->y+t->height,1,1)-(s->by-oy);
  s->has_bounds=1;
}
static void keymap_hints_clear(struct object *s) {
  if(!s||!s->hints)return;
  scrcpy_mapping_cancel();
  for(int i=0;i<24;++i)s->hints->active_keys[i]=0;
  memset(s->hints->flash_ticks,0,sizeof(s->hints->flash_ticks));
  s->hints->dirty=1;
  keymap_wake();
}
/* Window controls apply immediately; they do not modify the saved bindings. */
static void keymap_toggle(struct object *s, int visibility) {
  if (!s) return;
  if (visibility) s->keymap_hidden = !s->keymap_hidden;
  else {
    s->keymap_disabled = !s->keymap_disabled;
    if (s->hints) {
      /* A key-down consumed by the mapper must not leak its key-up to Android. */
      for (struct object *o=objects;o;o=o->next)
        if (o->keyboard_focus==s)
          for(int i=0;i<24;++i)o->keymap_blocked_keys[i]|=s->hints->active_keys[i];
      keymap_hints_clear(s);
    }
  }
  struct keymap_overlay *layers[]={s->hints,s->overlay};
  for(int i=0;i<2;++i)if(layers[i]) {
    layers[i]->disabled=s->keymap_disabled;
    layers[i]->hints_hidden=s->keymap_hidden;
    layers[i]->dirty=1;
  }
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
  if(s->overlay&&s->overlay->token&&keymap_task_owner(s)!=s->overlay->task) {
    /* A replaced or ambiguous task must never inherit an in-flight edit. */
    s->overlay->close_pending=1;
    s->overlay->close_result=3;
    s->overlay->save_pending=0;
  }
  if(s->overlay) {
    int status=keymap_capture_status(s);
    if(status!=s->overlay->capture_status){s->overlay->capture_status=status;s->overlay->dirty=1;}
  }
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
  if(f->panel.dock_pending)keymap_panel_destroy(f);
  if(f->dirty&&f->detached)f->panel.dirty=1;
  struct csd_piece *p=&f->piece;
  if(p->width!=w||p->height!=h||
     (f->sync_valid&&(f->sync_x!=x||f->sync_y!=y))) {
    /* A release after a geometry change belongs to the old layout. It cannot
     * finish a drag or activate Save/Delete at a newly positioned hit target. */
    f->dragging=0;f->deferred_binding=0;
    for(struct object *o=objects;o;o=o->next)
      if(o->overlay_focus==s)o->overlay_pressed=0;
    scrcpy_mapping_cancel();
  }
  if (!csd_compositor || !csd_subcompositor || !csd_shm)
    return;
  /* Passive markers have no inspector and clip their paint to the fitted
   * canvas. Rotation/letterboxing can legitimately make that canvas smaller
   * than the interactive editor's minimum; its hints must follow the fit. */
  int minimum_w=f->passive?1:300,minimum_h=f->passive?1:120;
  if (w < minimum_w || h < minimum_h || w > 8192 || h > 8192 || (int64_t)w * h > 16777216) {
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
    if(!f->passive)csd_input(p->surface, f->testing?12:0, f->testing?12:0, f->testing?184:w, f->testing?30:h);
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
  if(f->detached)keymap_panel_sync(f);
  f->sync_valid=1;f->sync_x=x;f->sync_y=y;f->sync_above=above_surface;
}
static int keymap_overlay_enter(struct object *pointer, struct proxy *surface) {
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay && (s->overlay->piece.surface == surface||s->overlay->panel.piece.surface==surface)) {
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
  if(keymap_panel_owner(pointer->pointer_surface)==s)return;
  if(f->testing)return;
  if(f->model.selected<0||f->model.selected>=f->model.count)f->dragging=0;
  struct keymap_layout l=f->detached?(struct keymap_layout){0}:keymap_layout(f->piece.width,f->piece.height,f->panel_hidden);
  if (f->dragging && !f->save_pending &&
      !keymap_ui_hit(l,pointer->csd_x,pointer->csd_y)) {
    int pdx=pointer->csd_x-f->press_x,pdy=pointer->csd_y-f->press_y;
    if(!f->drag_moved&&pdx<=3&&pdx>= -3&&pdy<=3&&pdy>= -3)return;
    if(f->binding_capture){
      f->binding_capture=0;f->deferred_binding=0;
      f->model.error=keymap_valid(&f->model,0)?0:1;
    }
    struct keymap_point *q=&f->model.points[f->model.selected];
    int nx=keymap_normalize(pointer->csd_x,f->piece.width),ny=keymap_normalize(pointer->csd_y,f->piece.height);
    if(f->dragging==2){q->ex=nx;q->ey=ny;}
    else if(f->dragging==3){int cx=(int)((int64_t)q->nx*(f->piece.width-1)/65535),d=pointer->csd_x-cx;
      if(d<0)d=-d;
      q->radius=(int)((int64_t)d*65535/(f->piece.width<f->piece.height?f->piece.width:f->piece.height));
      if(q->radius<256)q->radius=256;
      if(q->radius>32767)q->radius=32767;}
    else if(f->dragging==4){int dx=nx-q->nx,dy=ny-q->ny;if(dx<0)dx=-dx;if(dy<0)dy=-dy;
      int mx=q->nx<32768?q->nx:65535-q->nx,my=q->ny<32768?q->ny:65535-q->ny;
      q->look_width=dx<256?256:dx>mx?mx:dx;q->look_height=dy<256?256:dy>my?my:dy;}
    else {if(q->kind==KEYMAP_LOOK){if(nx<q->look_width)nx=q->look_width;if(nx>65535-q->look_width)nx=65535-q->look_width;if(ny<q->look_height)ny=q->look_height;if(ny>65535-q->look_height)ny=65535-q->look_height;}
      q->nx=nx;q->ny=ny;}
    int dx=pointer->csd_x-f->press_x,dy=pointer->csd_y-f->press_y;
    f->drag_moved=f->drag_moved||dx>3||dx< -3||dy>3||dy< -3;
    f->model.dirty=1;
    f->dirty = 1;
    keymap_wake();
  }
}
static int keymap_overlay_axis(struct object *pointer,uint32_t axis,int32_t value) {
  struct keymap_overlay *f=pointer->overlay_focus?pointer->overlay_focus->overlay:0;
  if(!f||f->testing)return 0;
  if(axis==0&&value){keymap_editor_scroll(f,value>0?1:-1);f->dirty=1;keymap_wake();}
  return 1;
}
static void keymap_overlay_pointer_cancel(struct object *pointer) {
  if (pointer->overlay_focus && pointer->overlay_focus->overlay){
    pointer->overlay_focus->overlay->dragging = 0;
    pointer->overlay_focus->overlay->deferred_binding = 0;
  }
  pointer->overlay_pressed = 0;
}
static void keymap_editor_testing(struct keymap_overlay *f,int testing) {
  scrcpy_mapping_cancel();
  if(testing)for(struct object *o=objects;o;o=o->next)if(o->overlay==f)o->keymap_auto_paused=0;
  f->testing=testing;f->binding_capture=0;f->numeric_capture=0;f->type_open=0;f->dragging=0;
  f->dirty=1;keymap_wake();
}
static void keymap_editor_clone(struct keymap_overlay *f) {
  struct keymap_model *m=&f->model;
  if(!keymap_editor_can_clone(f))return;
  struct keymap_point q=m->points[m->selected];
  keymap_history_init(f);
  q.nx=q.nx<61439?q.nx+4096:q.nx-4096;q.ny=q.ny<61439?q.ny+4096:q.ny-4096;
  q.key=0;q.drag_shot=0;q.walk_key=0;q.walk_radius=0;q.cancel_key=0;q.cancel_x=0;q.cancel_y=0;
  if(q.kind==KEYMAP_DPAD){q.up_key=0;q.left_key=0;q.down_key=0;q.right_key=0;q.key=17;}
  m->points[m->count]=q;m->selected=m->count++;m->dirty=1;m->error=keymap_valid(m,0)?0:1;
  f->capture_field=KP_BIND;f->binding_capture=q.kind!=KEYMAP_DPAD;f->inspector_scroll=0;
  keymap_history_commit(f);
}
static void keymap_editor_fps(struct keymap_overlay *f,int x,int y) {
  struct keymap_model *m=&f->model;
  if(m->count>KEYMAP_POINTS-2)return;
  struct keymap_model old=*m;
  if(!keymap_add(m,x,y,f->piece.width,f->piece.height))return;
  struct keymap_point *q=&m->points[m->selected];keymap_set_kind(q,KEYMAP_LOOK);
  q->key=63;q->source=KEYMAP_SOURCE_KEY;q->activation=KEYMAP_CAMERA_AUTO;
  q->suspend_key=56;
  keymap_add(m,f->piece.width*4/5,f->piece.height*3/4,f->piece.width,f->piece.height);
  q=&m->points[m->selected];keymap_set_kind(q,KEYMAP_BASIC);
  q->key=272;q->source=KEYMAP_SOURCE_MOUSE;keymap_editor_set_end(q,1);
  if(!keymap_valid(m,1)){*m=old;m->error=1;return;}
  m->selected=m->count-2;m->dirty=1;f->binding_capture=0;m->placing=0;
}
static void keymap_overlay_button(struct object *pointer,uint32_t button,uint32_t state) {
  struct object *s=pointer->overlay_focus;if(!s||!s->overlay)return;
  struct keymap_overlay *f=s->overlay;struct keymap_model *m=&f->model;
  if(f->save_pending||f->close_pending)return;
  int x=pointer->csd_x,y=pointer->csd_y;
  int panel=keymap_panel_owner(pointer->pointer_surface)==s;
  if(panel){y-=keymap_panel_header(f);if(y<0)return;}
  if(f->testing){
    if(button==272&&!state&&keymap_contains((struct keymap_rect){12,12,184,30},x,y))keymap_editor_testing(f,0);
    return;
  }
  struct keymap_layout l=f->detached&&!panel?(struct keymap_layout){0}:
    keymap_layout(panel?f->panel.width:f->piece.width,panel?f->panel.height-keymap_panel_header(f):f->piece.height,f->panel_hidden);
  /* Toolbar actions remain usable while a capture prompt is open. The event
   * that opens capture is a release. A left press on a marker stays a drag
   * candidate until release; only a click without movement binds Mouse1. */
  int hit=f->detached&&!panel?(keymap_contains((struct keymap_rect){12,12,60,26},x,y)?17:0):
    f->type_open?keymap_type_hit(f,x,y):keymap_ui_hit(l,x,y);
  if(!hit&&!panel)hit=keymap_point_hit(m,f->piece.width,f->piece.height,x,y);
  if(f->binding_capture&&state&&button>=272&&button<=276&&
     (button!=272||hit==0)) {
    if(keymap_editor_assign(f,KEYMAP_SOURCE_MOUSE,(int)button))f->binding_capture=0;
    f->dirty=1;keymap_wake();return;
  }
  if(button!=272)return;
  if(!hit&&!panel)hit=keymap_point_hit(m,f->piece.width,f->piece.height,x,y);
  if(state){
    f->deferred_binding=f->binding_capture&&hit>=200&&hit<700&&hit%100==m->selected;
    if(f->binding_capture&&hit>=200&&hit<700&&!f->deferred_binding)f->binding_capture=0;
    if(f->numeric_capture&&hit==2){keymap_numeric_apply(f);if(f->numeric_capture){f->dirty=1;keymap_wake();return;}}
    else if(hit==1||hit==3||hit==5||hit>=100)f->numeric_capture=0;
    pointer->overlay_pressed=hit;f->press_x=x;f->press_y=y;f->drag_moved=0;
    keymap_history_init(f);
    if(hit==0&&m->placing&&!panel){
      if(f->placement_kind==10)keymap_editor_fps(f,x,y);
      else if(keymap_add(m,x,y,f->piece.width,f->piece.height)){
        keymap_set_kind(&m->points[m->selected],f->placement_kind);
        if(f->placement_kind==KEYMAP_LOOK){
          struct keymap_point *q=&m->points[m->selected];
          q->key=63;q->source=KEYMAP_SOURCE_KEY;q->activation=KEYMAP_CAMERA_AUTO;
          q->suspend_key=56;
          if(!keymap_valid(m,0))q->suspend_key=0;
        }
        if(f->placement_hold&&(f->placement_kind==KEYMAP_BASIC||f->placement_kind==KEYMAP_SWIPE))keymap_editor_set_end(&m->points[m->selected],1);
        f->placement_release=1;
      }
      f->inspector_scroll=0;f->dirty=1;keymap_wake();
    }else if(hit>=200&&hit<700){
      m->selected=hit%100;f->inspector_scroll=0;
      f->dragging=hit>=600?1:hit>=500?4:hit>=400?3:hit>=300?2:1;
      f->dirty=1;keymap_wake();
    }else if(!hit&&!panel){m->selected=-1;f->binding_capture=0;f->dirty=1;keymap_wake();}
    return;
  }
  int dragged=f->drag_moved,deferred=f->deferred_binding;f->dragging=0;f->deferred_binding=0;
  if(deferred&&f->binding_capture&&!dragged&&hit==pointer->overlay_pressed){
    if(keymap_editor_assign(f,KEYMAP_SOURCE_MOUSE,272))f->binding_capture=0;
  }else if(f->placement_release){
    f->placement_release=0;
    if(m->selected>=0&&m->points[m->selected].kind!=KEYMAP_DPAD&&m->points[m->selected].source==KEYMAP_SOURCE_KEY&&
       !(m->points[m->selected].kind==KEYMAP_LOOK&&m->points[m->selected].activation==KEYMAP_CAMERA_AUTO)){f->binding_capture=1;f->capture_field=KP_BIND;}
  }else if(hit&&hit==pointer->overlay_pressed&&!dragged){
    if(hit==-2)f->type_open=0;
    else if(hit>=40&&hit<40+keymap_palette_count())keymap_editor_choose_kind(f,hit-40);
    else if(hit>=20&&hit<36){struct keymap_rect r=l.properties[hit-20];keymap_property_action(f,hit-20,x<r.x+32?-1:x>=r.x+r.w-32?1:0);}
    else if(hit>=600&&hit<600+KEYMAP_POINTS){
      if(m->points[m->selected].kind!=KEYMAP_DPAD&&m->points[m->selected].source!=KEYMAP_SOURCE_POINTER){f->binding_capture=1;f->capture_field=KP_BIND;}
    }else if(hit>=200&&hit<300){
      long now=map_monotonic_ms();
      if(f->last_click_ms&&now-f->last_click_ms<=400&&f->last_click_index==m->selected&&m->points[m->selected].kind!=KEYMAP_DPAD&&m->points[m->selected].source!=KEYMAP_SOURCE_POINTER){f->binding_capture=1;f->capture_field=KP_BIND;f->last_click_ms=0;}
      else{f->last_click_ms=now;f->last_click_index=m->selected;}
    }else if(hit==1){f->type_open=!f->type_open;f->binding_capture=0;f->palette_scroll=0;}
    else if(hit==2){if(keymap_valid(m,1))f->save_pending=1;else m->error=2;}
    else if(hit==3||(hit>=100&&hit<100+KEYMAP_POINTS)){
      if(hit>=100)m->selected=hit-100;
      keymap_remove(m);f->binding_capture=0;f->inspector_scroll=0;
    }else if(hit==5){f->ack_pending=3;f->close_pending=1;s->overlay_dismissed=1;}
    else if(hit==6)f->panel_hidden=!f->panel_hidden;
    else if(hit==9||hit==10)keymap_toggle(s,hit==10);
    else if(hit==11||hit==12)keymap_history_step(f,hit==11?-1:1);
    else if(hit==13)keymap_editor_clone(f);
    else if(hit==14){if(keymap_valid(m,1))keymap_editor_testing(f,1);else m->error=2;}
    else if(hit==15||hit==16)keymap_editor_scroll(f,hit==15?-1:1);
    else if(hit==17){if(f->detached)f->panel.dock_pending=1;else if(!keymap_panel_create(s))m->error=2;}
  }
  keymap_history_commit(f);pointer->overlay_pressed=0;f->dirty=1;keymap_wake();
}
static struct game_fit_rect game_fit_area(struct object *);
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
      if(s->full_ui)keymap_canvas_bounds(s);
      if(!s->full_ui&&window_fit_enabled(s)&&s->fit&&
         s->fit_sw>0&&s->fit_sh>0&&s->fit_w>0&&s->fit_h>0) {
        /* Flash repaints must use the same fitted content rectangle as HWC
         * geometry commits. Container bounds include letterboxing; swapping
         * between them cancels held contacts as though a resize occurred. */
        struct game_fit_rect r=game_fit_area(s);
        keymap_overlay_sync(s,r.x,r.y,r.width,r.height);
      } else keymap_overlay_sync(s,s->bx,s->by,s->bw,s->bh);
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
static struct object *game_fit_bar_owner(struct proxy *);
static struct object *keymap_owner(struct proxy *p) {
  if(!p)return 0;
  struct object *panel_owner=keymap_panel_owner(p);
  if(panel_owner)return panel_owner;
  /* These bridge-created children bypass the upstream object registry, but
   * Weston may focus them after a click. Their keyboard owner is still the
   * app that owns the editor, menu, frame, or letterbox. */
  for(struct object *owner=objects;owner;owner=owner->next)if(owner->app||owner->full_ui) {
    if((owner->overlay&&owner->overlay->piece.surface==p)||
       (owner->hints&&owner->hints->piece.surface==p))return owner;
    if(owner->csd){
      if(owner->csd->menu.surface==p)return owner;
      for(int i=0;i<4;++i)
        if(owner->csd->pieces[i].surface==p||owner->csd->bars[i].surface==p)return owner;
    }
  }
  struct object *bar_owner=game_fit_bar_owner(p);
  if(bar_owner)return bar_owner;
  struct object *s = find(p);
  if (!s)
    return 0;
  if (s && (s->app||s->full_ui))
    return s;
  for (struct object *o = objects; o; o = o->next)
    if (o->surface == s && o->parent && (o->parent->app||o->parent->full_ui) &&
        object_is(o,"wl_subsurface"))
      return o->parent;
  return 0;
}
static void keymap_kmap(void *data, struct proxy *p, uint32_t format,
                        int32_t fd, uint32_t size) {
  struct object *o = data;
  ((void (*)(void *, struct proxy *, uint32_t, int32_t,
             uint32_t))o->listener[0])(o->listener_data, p, format, fd, size);
}
static void keymap_finish_keyboard_leave(struct object *o) {
  if(!o->keyboard_leave_pending)return;
  o->keyboard_leave_pending=0;
  o->keyboard_leave_surface=0;
  /* Try can own contacts before this app has any saved hints. A confirmed
   * keyboard blur must release those contacts independently of hint state. */
  scrcpy_mapping_cancel();
  keymap_shortcut_task=0;
  if(o->keyboard_focus&&o->keyboard_focus->csd&&o->keyboard_focus->csd->menu_open)
    csd_menu_close(o->keyboard_focus->csd);
  keymap_hints_clear(o->keyboard_focus);
  o->keyboard_focus=0;
  for(int i=0;i<24;++i)o->keymap_blocked_keys[i]=0;
}
/* Weston may transfer keyboard focus from an app root to one of its child
 * surfaces immediately before a click. Resolve that leave/enter pair by app
 * owner within one dispatch batch; an unmatched leave still cancels before
 * dispatch returns, without a timer or a grace period for real focus loss. */
static void keymap_finish_keyboard_leaves(struct wl_display *display) {
  for(struct object *o=objects;o;o=o->next)
    if(o->display==display)keymap_finish_keyboard_leave(o);
}
static void scrcpy_mapping_reconcile_keys(const uint32_t *,size_t);
static void keymap_kenter(void *data, struct proxy *p, uint32_t serial,
                          struct proxy *surface, void *keys) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  clipboard_input(serial);
  struct object *owner=keymap_owner(surface);
  int panel=keymap_panel_owner(surface)!=0;
  int panel_transition=panel||o->keymap_panel_focus;
  if(o->keyboard_leave_pending&&o->keyboard_focus==owner&&owner&&
     o->keyboard_leave_surface!=surface) {
    o->keyboard_leave_pending=0;
    o->keyboard_leave_surface=0;
  } else {
    keymap_finish_keyboard_leave(o);
    keymap_hints_clear(o->keyboard_focus);
    /* wl_keyboard.enter lists keys already down. After a real blur they
     * must be released before a mapped key can start another contact. */
    struct { size_t size,alloc;void *data; } *held=keys;
    int snapshot_valid=held&&held->size<=768*sizeof(uint32_t)&&
      !(held->size%sizeof(uint32_t))&&(!held->size||held->data);
    if(snapshot_valid)for(size_t i=0;i<held->size/sizeof(uint32_t);++i)
      if(((uint32_t *)held->data)[i]>=768)snapshot_valid=0;
    if(snapshot_valid) {
      scrcpy_mapping_reconcile_keys(held->data,held->size/sizeof(uint32_t));
      memset(o->keymap_blocked_keys,0,sizeof(o->keymap_blocked_keys));
      for(size_t i=0;i<held->size/sizeof(uint32_t);++i) {
        uint32_t key=((uint32_t *)held->data)[i];
        if(key<768)o->keymap_blocked_keys[key/32]|=1u<<(key%32);
      }
    } /* An invalid/missing snapshot cannot prove that an old key was released. */
  }
  /* Editing shares one draft across both windows. Gameplay contacts never
   * follow focus into the tools window, including a paired same-owner enter. */
  if(panel_transition)scrcpy_mapping_cancel();
  o->keyboard_focus=owner;
  o->keymap_panel_focus=panel;
  pthread_mutex_unlock(mutex);
  if(panel)return;
  ((void (*)(void *, struct proxy *, uint32_t, struct proxy *,
             void *))o->listener[1])(o->listener_data, p, serial, surface,
                                     keys);
}
static void keymap_kleave(void *data, struct proxy *p, uint32_t serial,
                          struct proxy *surface) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  o->keyboard_leave_pending=1;
  o->keyboard_leave_surface=surface;
  int panel=o->keymap_panel_focus||keymap_panel_owner(surface)!=0;
  pthread_mutex_unlock(mutex);
  if(panel)return;
  ((void (*)(void *, struct proxy *, uint32_t, struct proxy *))o->listener[2])(
      o->listener_data, p, serial, surface);
}
static void keymap_key(void *data, struct proxy *p, uint32_t serial,
                       uint32_t time, uint32_t key, uint32_t state) {
  struct object *o = data;
  pthread_mutex_lock(mutex);
  clipboard_input(serial);
  keymap_finish_keyboard_leave(o);
  if(!state)scrcpy_mapping_release_key((int)key);
  if(key==1 && scrcpy_mapping_escape()) {
    if(state&&o->keyboard_focus&&o->keyboard_focus->overlay&&o->keyboard_focus->overlay->testing)keymap_editor_testing(o->keyboard_focus->overlay,0);
    if(state)o->keymap_blocked_keys[key/32]|=1u<<(key%32);
    else o->keymap_blocked_keys[key/32]&=~(1u<<(key%32));
    pthread_mutex_unlock(mutex);return;
  }
  if(csd_menu_key(o,key,state)){pthread_mutex_unlock(mutex);return;}
  struct keymap_overlay *f = o->keyboard_focus ? o->keyboard_focus->overlay : 0;
  int blocked =
      key < 768 && (o->keymap_blocked_keys[key / 32] & (1u << (key % 32)));
  /* A detached tools window is never an Android input target. Its editor
   * keys work normally; while Try is active only Escape/F2 return to editing. */
  if(o->keymap_panel_focus&&(!f||!f->panel.piece.surface||f->testing)) {
    if(f&&state&&(key==1||key==60))keymap_editor_testing(f,0);
    if(key<768) {
      if(state)o->keymap_blocked_keys[key/32]|=1u<<(key%32);
      else o->keymap_blocked_keys[key/32]&=~(1u<<(key%32));
    }
    pthread_mutex_unlock(mutex);return;
  }
  if(f&&f->testing) {
    if(blocked){if(!state)o->keymap_blocked_keys[key/32]&=~(1u<<(key%32));pthread_mutex_unlock(mutex);return;}
    if(key==1||key==60){if(state)keymap_editor_testing(f,0);pthread_mutex_unlock(mutex);return;}
    if(key==61||key==62){if(state){o->keymap_blocked_keys[key/32]|=1u<<(key%32);keymap_toggle(o->keyboard_focus,key==62);}pthread_mutex_unlock(mutex);return;}
    int mapped=scrcpy_mapping_key(o->keyboard_focus,f,(int)key,state);
    pthread_mutex_unlock(mutex);
    if(!mapped)((void (*)(void *,struct proxy *,uint32_t,uint32_t,uint32_t,uint32_t))o->listener[3])(o->listener_data,p,serial,time,key,state);
    return;
  }
  if (f && key < 768) {
    if (state)
      o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
  }
  if (!state && key < 768)
    o->keymap_blocked_keys[key / 32] &= ~(1u << (key % 32));
  if (ANVIL_KEYMAP_EDITOR && (key == 61 || key == 62) &&
      o->keyboard_focus && keymap_task_owner(o->keyboard_focus)) {
    if (state && !blocked) {
      o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
      keymap_toggle(o->keyboard_focus, key == 62);
    }
    pthread_mutex_unlock(mutex);
    return;
  }
  /* Queue disk IO through the same validated path as the library editor. */
  if (ANVIL_KEYMAP_EDITOR && key == 60 && o->keyboard_focus) {
    struct object *s = o->keyboard_focus;
    if (state && !blocked) {
      o->keymap_blocked_keys[key / 32] |= 1u << (key % 32);
      if (f) {
        if (!f->save_pending) {
          f->close_pending = 1;
          f->ack_pending = 3;
          keymap_wake();
        }
      } else if (keymap_shortcut_task) {
        keymap_shortcut_task = 0;
      } else if (keymap_connection_ready() && keymap_task_owner(s)) {
        keymap_shortcut_task = keymap_task_owner(s);
        snprintf(keymap_shortcut_package, sizeof(keymap_shortcut_package),
                 "%s", keymap_package(s));
        keymap_hints_clear(s);
      }
    }
    pthread_mutex_unlock(mutex);
    return;
  }
  if (f && state && !f->save_pending && !f->close_pending) {
    keymap_history_init(f);
    if(f->numeric_capture) {
      if(key==1)f->numeric_capture=0;
      else if(key==28)keymap_numeric_apply(f);
      else if(key==14){f->numeric_value/=10;if(f->numeric_digits)f->numeric_digits--;}
      else if(key>=2&&key<=11&&f->numeric_digits<5){int digit=key==11?0:(int)key-1;f->numeric_value=f->numeric_value*10+digit;f->numeric_digits++;}
    } else if(f->type_open) {
      if(key==1)f->type_open=0;
    } else if(f->binding_capture&&key==111&&f->capture_field!=KP_BIND){
      keymap_editor_clear_optional(f);
    } else if(f->binding_capture&&key!=1){
      if(keymap_editor_assign(f,KEYMAP_SOURCE_KEY,(int)key))f->binding_capture=0;
    } else if (key == 1 && f->binding_capture) {
      f->binding_capture=0;
    } else if(key==104||key==109) {
      keymap_editor_scroll(f,key==104?-1:1);
    } else if(key==14) {
      keymap_history_step(f,-1);
    } else if(key==111) {
      keymap_history_init(f);keymap_remove(&f->model);keymap_history_commit(f);
    } else if (key == 1) {
      f->close_pending = 1;
      f->ack_pending = 3;
    } else if (key==64 && f->model.selected>=0) { /* F6 cycles functional control presets. */
      struct keymap_point *q=&f->model.points[f->model.selected];
      keymap_set_kind(q,(q->kind+1)%(KEYMAP_LOOK+1));
      f->property_page=0;
      f->model.dirty=1;
    } else if((key==66||key==67) && f->model.selected>=0) {
      struct keymap_point *q=&f->model.points[f->model.selected];
      if(q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_REPEAT||q->kind==KEYMAP_MACRO) {
        if(key==66)q->duration=q->duration<100?100:q->duration<250?250:q->duration<500?500:q->duration<1000?1000:50;
        else if(q->kind==KEYMAP_MACRO)q->repeats=q->repeats%5+1;
        while(q->kind==KEYMAP_MACRO&&q->duration*q->repeats*2>10000)--q->repeats;
        f->model.dirty=1;
      }
    } else if(key==65 && f->model.selected>=0) {
      struct keymap_point *q=&f->model.points[f->model.selected];
      for(struct object *pointer=objects;pointer;pointer=pointer->next)
        if(pointer->overlay_focus==o->keyboard_focus) {
          q->ex=keymap_normalize(pointer->csd_x,f->piece.width);
          q->ey=keymap_normalize(pointer->csd_y,f->piece.height);f->model.dirty=1;break;
        }
    }
    keymap_history_commit(f);
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
  if(!suppress&&hints&&!o->keyboard_focus->keymap_disabled&&key<768) {
    if (scrcpy_mapping_key(o->keyboard_focus, hints, (int)key, state))
      suppress = 1;
    for(int i=0;i<hints->model.count;++i)if(hints->model.points[i].key==(int)key ||
      (hints->model.points[i].kind==KEYMAP_DPAD && (key==17||key==30||key==31||key==32))) {
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
  int blocked = o->keymap_panel_focus || (o->keyboard_focus && o->keyboard_focus->overlay && !o->keyboard_focus->overlay->testing);
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
  clipboard_input(serial);
  for (int i = 0; i < 32; ++i)
    if (!(o->keymap_touch_present & (1u << i))) {
      slot = i;
      break;
    }
  for (struct object *s = objects; s; s = s->next)
    if (s->overlay && s->overlay->piece.surface == surface)
      blocked = 1;
  if(keymap_panel_owner(surface))blocked=1;
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
  x=content_clip_input(surface,x,0);y=content_clip_input(surface,y,1);
  x=full_ui_input(surface,x,0);y=full_ui_input(surface,y,1);
  x=game_fit_input(surface,x,0);y=game_fit_input(surface,y,1);
  app_rotation_input(surface,&x,&y);
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
  if(slot>=0){x=content_clip_input(o->touch_surfaces[slot],x,0);y=content_clip_input(o->touch_surfaces[slot],y,1);}
  if(slot>=0){x=full_ui_input(o->touch_surfaces[slot],x,0);y=full_ui_input(o->touch_surfaces[slot],y,1);}
  if(slot>=0){x=game_fit_input(o->touch_surfaces[slot],x,0);y=game_fit_input(o->touch_surfaces[slot],y,1);app_rotation_input(o->touch_surfaces[slot],&x,&y);}
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
  s->keymap_auto_paused=0;
  s->hints_task=task;
  if(!s->hints&&model->count)s->hints=calloc(1,sizeof(*s->hints));
  if(!s->hints)return;
  struct keymap_overlay *f=s->hints;
  f->passive=1;f->model=*model;f->model.selected=-1;
  f->disabled=s->keymap_disabled;f->hints_hidden=s->keymap_hidden;
  f->task=task;f->dirty=1;f->close_pending=!model->count;
  snprintf(f->package,sizeof(f->package),"%s",keymap_package(s));
  for(int i=0;i<24;++i)f->active_keys[i]=0;
  memset(f->flash_ticks,0,sizeof(f->flash_ticks));
  keymap_wake();
}
/* One profile read per poll, outside the event-thread lock. Bind completion
 * to a never-reused token + current task/package, not a borrowed object pointer. */
static void keymap_hints_poll(const char *runtime) {
  char pkg[241]="";unsigned long token=0;int task=0;
  pthread_mutex_lock(mutex);
  scrcpy_mapping_tick();
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
    if(s->full_ui)keymap_canvas_bounds(s);
    if(s->hints_task!=owner&&!s->hints)s->hints_task=0;
    if(s->hints&&owner!=s->hints->task) {
      s->hints->close_pending=1;keymap_wake();
    }
    if(!token&&owner&&!s->overlay&&!s->hints&&s->hints_task!=owner&&
       profile_name(keymap_package(s),1)) {
      s->hints_task=owner;token=s->hints_probe=++keymap_token;task=owner;
      snprintf(pkg,sizeof(pkg),"%s",keymap_package(s));
    }
  }
  pthread_mutex_unlock(mutex);
  if(!token)return;
  struct keymap_model model={.selected=-1};
  int dir=open(KEYMAP_BASE "/keymaps",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  const char *generation=getenv("ANVILDROID_RUNTIME_GENERATION");
  int locked=generation&&keymap_scheme_lock(generation);
  int loaded=locked&&dir>=0?keymap_load(dir,pkg,runtime,&model):-2;
  if(dir>=0)close(dir);
  if(locked)keymap_scheme_unlock();
  pthread_mutex_lock(mutex);
  for(struct object *s=objects;s;s=s->next)
    if(s->hints_probe==token&&keymap_package(s)&&!strcmp(keymap_package(s),pkg)&&
       !s->overlay&&keymap_task_owner(s)==task) {
      if(loaded==1)keymap_hints_set(s,&model,task);
      else if(loaded==-2)s->hints_task=0;
      break;
    }
  pthread_mutex_unlock(mutex);
}
/* The scheme library and editor share an explicit exclusion protocol. Create
 * the session marker before loading, then recheck the library mutation lock.
 * The backend performs the inverse order. Neither can publish a stale draft. */
static int keymap_worker_file(const char *name,const char *data) {
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return 0;
  char temp[96];snprintf(temp,sizeof(temp),".%s-%d",name,getpid());
  int fd=openat(dir,temp,1|PROFILE_CREAT|01000|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
  int n=(int)strlen(data),ok=fd>=0&&write(fd,data,(unsigned long)n)==n;
  if(fd>=0)close(fd);
  if(ok)ok=!renameat(dir,temp,dir,name);
  if(!ok)unlinkat(dir,temp,0);
  close(dir);return ok;
}
/* proc start time distinguishes a restarted bridge from a reused PID. */
static int keymap_process_start(int pid,char *out,int size) {
  char path[64],data[2048];snprintf(path,sizeof(path),"/proc/%d/stat",pid);
  int fd=open(path,PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  if(fd<0)return PROFILE_ERRNO==2?0:-1;
  long n=read(fd,data,sizeof(data)-1);close(fd);if(n<=0)return -1;data[n]=0;
  const char *end=0;for(const char *c=data;*c;++c)if(*c==')')end=c;
  if(!end)return -1;
  const char *cursor=end+1;
  for(int field=3;field<=22;++field){
    while(*cursor==' ')++cursor;
    const char *start=cursor;while(*cursor&&*cursor!=' '&&*cursor!='\n')++cursor;
    if(cursor==start)return -1;
    if(field==22){int len=(int)(cursor-start);if(len>=size)return -1;
      for(int i=0;i<len;++i){if(start[i]<'0'||start[i]>'9')return -1;out[i]=start[i];}out[len]=0;return 1;}
  }
  return -1;
}
/* Native read/session locks never carry a rollback journal. Recover only a
 * proven stale PID-only lock; a backend transaction must recover itself. */
static int keymap_scheme_recover(const char *generation) {
  int dir=open(KEYMAP_BASE "/keymaps",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return 0;
  int lock=openat(dir,".scheme-lock",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(lock<0){close(dir);return PROFILE_ERRNO==2;}
  int fd=openat(lock,"pid",PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  if(fd<0){close(lock);close(dir);return 0;}
  char data[128],pid_text[16],start[32],gen[33],current[32];
  long n=read(fd,data,sizeof(data)-1);close(fd);int used=0,stale=0;
  if(n>0){data[n]=0;
    if(sscanf(data,"%15s %31s %32s %n",pid_text,start,gen,&used)==3&&!data[used]){
      int pid=keymap_number(pid_text,2147483647);
      if(pid>0){int state=keymap_process_start(pid,current,sizeof(current));
        stale=strcmp(gen,generation)||!state||(state==1&&strcmp(start,current));}
    }
  }
  const char *journal[]={"pid-tmp","ready","record","new-a","new-m","old-a","old-m","restore-a","restore-m","reload"};
  for(unsigned i=0;stale&&i<sizeof(journal)/sizeof(journal[0]);++i){
    fd=openat(lock,journal[i],PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
    if(fd>=0){close(fd);stale=0;}else if(PROFILE_ERRNO!=2)stale=0;
  }
  int recovered=0;
  if(stale&&!unlinkat(lock,"pid",0)){
    recovered=!unlinkat(dir,".scheme-lock",0x200);
    if(!recovered){ /* An unexpected entry is never deleted. Restore ownership. */
      fd=openat(lock,"pid",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
      if(fd>=0){write(fd,data,(unsigned long)n);close(fd);}
    }
  }
  close(lock);close(dir);return recovered;
}
static int keymap_scheme_lock(const char *generation) {
  if(mkdir(KEYMAP_BASE "/keymaps/.scheme-lock",0700)){
    if(PROFILE_ERRNO!=17||!keymap_scheme_recover(generation)||
       mkdir(KEYMAP_BASE "/keymaps/.scheme-lock",0700))return 0;
  }
  char start[32],data[112];int ok=keymap_process_start(getpid(),start,sizeof(start))==1;
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  int fd=dir>=0?openat(dir,"keymaps/.scheme-lock/pid",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600):-1;
  int n=ok?snprintf(data,sizeof(data),"%d %s %s\n",getpid(),start,generation):0;
  ok=ok&&fd>=0&&write(fd,data,(unsigned long)n)==n;
  if(fd>=0)close(fd);
  if(dir>=0)close(dir);
  if(!ok)keymap_scheme_unlock();
  return ok;
}
static void keymap_scheme_unlock(void) {
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return;
  unlinkat(dir,"keymaps/.scheme-lock/pid",0);
  unlinkat(dir,"keymaps/.scheme-lock",0x200);close(dir);
}
static int keymap_marker_stale(int dir,const char *generation) {
  int fd=openat(dir,"keymap-editing",PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  if(fd<0)return PROFILE_ERRNO==2;
  char data[384],gen[33],pkg[241],pid_text[16],start[32]="",current[32];
  long n=read(fd,data,sizeof(data)-1);close(fd);if(n<=0)return 0;data[n]=0;
  int used=0,fields=sscanf(data,"%32s %240s %15s %31s %n",gen,pkg,pid_text,start,&used);
  if(fields!=4||data[used]){start[0]=0;used=0;if(sscanf(data,"%32s %240s %15s %n",gen,pkg,pid_text,&used)!=3||data[used])return 0;}
  int pid=keymap_number(pid_text,2147483647);
  if(pid<=0||!profile_name(pkg,1))return 0;
  if(strcmp(gen,generation))return 1;
  int state=keymap_process_start(pid,current,sizeof(current));
  return !state||(state==1&&start[0]&&strcmp(start,current));
}
static int keymap_marker_begin(const char *generation,const char *pkg) {
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return 0;
  int recovered_lock=0;
  int fd=openat(dir,"keymap-editing",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
  if(fd<0&&PROFILE_ERRNO==17){
    recovered_lock=keymap_scheme_lock(generation);
    if(recovered_lock&&keymap_marker_stale(dir,generation)){
      unlinkat(dir,"keymap-editing",0);
      fd=openat(dir,"keymap-editing",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
    }
  }
  if(fd<0){if(recovered_lock)keymap_scheme_unlock();close(dir);return 0;}
  char start[32],text[352];int ok=keymap_process_start(getpid(),start,sizeof(start))==1;
  int n=ok?snprintf(text,sizeof(text),"%s %s %d %s\n",generation,pkg,getpid(),start):0;
  ok=ok&&write(fd,text,(unsigned long)n)==n&&!fsync(fd);close(fd);
  if(!recovered_lock){
    int lock=openat(dir,"keymaps/.scheme-lock",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
    if(lock>=0){close(lock);if(!keymap_scheme_recover(generation))ok=0;}else if(PROFILE_ERRNO!=2)ok=0;
  }
  if(!ok)unlinkat(dir,"keymap-editing",0);
  if(recovered_lock)keymap_scheme_unlock();
  close(dir);return ok;
}
static void keymap_marker_end(void) {
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return;
  unlinkat(dir,"keymap-editing",0);close(dir);
}
static void keymap_scheme_reload(const char *generation) {
  int dir=open(KEYMAP_BASE,PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(dir<0)return;
  int lock=openat(dir,"keymaps/.scheme-lock",PROFILE_DIRECTORY|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  if(lock>=0){close(lock);close(dir);return;}
  if(PROFILE_ERRNO!=2){close(dir);return;}
  int fd=openat(dir,"keymap-scheme-reload",PROFILE_NOFOLLOW|PROFILE_CLOEXEC|PROFILE_NONBLOCK);
  if(fd<0){close(dir);return;}
  char data[384],gen[33],pkg[241],nonce[65];int used=0;
  long n=read(fd,data,sizeof(data)-1);close(fd);
  int valid=0;if(n>0){data[n]=0;valid=sscanf(data,"%32s %240s %64s %n",gen,pkg,nonce,&used)==3&&!data[used]&&
    !strcmp(gen,generation)&&profile_name(pkg,1)&&profile_name(nonce,0);}
  pthread_mutex_lock(mutex);
  int editing=0;
  if(valid)for(struct object *o=objects;o;o=o->next)
    if(o->overlay)editing=1;
  if(valid&&!editing)for(struct object *o=objects;o;o=o->next)
    if(keymap_package(o)){
      if(o->hints){keymap_hints_clear(o);o->hints->close_pending=1;keymap_wake();}
      o->hints_task=0;
    }
  pthread_mutex_unlock(mutex);
  if(!editing)unlinkat(dir,"keymap-scheme-reload",0);
  close(dir);
}
static void keymap_io_poll(void) {
  if (!ANVIL_KEYMAP_EDITOR)
    return;
  const char *runtime = getenv("ANVILDROID_RUNTIME_ID"),
             *generation = getenv("ANVILDROID_RUNTIME_GENERATION");
  if (!profile_name(runtime, 0) || !generation)
    return;
  static char protocol_generation[33];
  if(strcmp(protocol_generation,generation)) {
    char protocol[48];snprintf(protocol,sizeof(protocol),"%s\n",generation);
    if(keymap_worker_file("keymap-scheme-protocol",protocol))snprintf(protocol_generation,sizeof(protocol_generation),"%s",generation);
  }
  pthread_mutex_lock(mutex);int clear_marker=keymap_marker_clear;keymap_marker_clear=0;pthread_mutex_unlock(mutex);
  if(clear_marker)keymap_marker_end();
  keymap_scheme_reload(generation);
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
  int shortcut = 0;
  pthread_mutex_lock(mutex);
  if (!*nonce && keymap_shortcut_task) {
    shortcut = 1;
    task = keymap_shortcut_task;
    snprintf(pkg, sizeof(pkg), "%s", keymap_shortcut_package);
  }
  keymap_shortcut_task = 0;
  pthread_mutex_unlock(mutex);
  if (*nonce || shortcut) {
    struct keymap_model model = {.selected = -1};
    mkdir(KEYMAP_BASE "/keymaps", 0700);
    int dir = open(KEYMAP_BASE "/keymaps",
                   PROFILE_DIRECTORY | PROFILE_NOFOLLOW | PROFILE_CLOEXEC);
    int marker_ok=keymap_marker_begin(generation,pkg);
    int valid = marker_ok&&dir >= 0 ? keymap_load(dir, pkg, runtime, &model) : -1;
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
      if(s->full_ui)keymap_canvas_bounds(s);
      if ((s->app||s->full_ui) && keymap_package(s) &&
          !strcmp(keymap_package(s),pkg) && keymap_task_owner(s)==task && s->has_bounds) {
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
    if (accepted && shortcut) {
      int focused = 0;
      for (struct object *o = objects; o; o = o->next)
        if (o->keyboard_focus == target) focused = 1;
      accepted = focused;
    }
    if (accepted) {
      target->overlay = calloc(1, sizeof(*target->overlay));
      accepted = target->overlay != 0;
      if (accepted) {
        struct keymap_overlay *f = target->overlay;
        f->model = model;
        f->baseline=model;f->baseline_valid=1;
        f->disabled=target->keymap_disabled;
        f->hints_hidden=target->keymap_hidden;
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
    if (!accepted) {
      if(marker_ok)keymap_marker_end();
      keymap_reply(nonce, "error");
    }
  }
  /* Snapshot one rare save/ack, then do file IO without blocking Wayland. */
  struct keymap_model model;
  unsigned long token = 0;
  int saving = 0, ack = 0, unchanged=0;
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
        if (matching != 1 || !owner || !keymap_package(s) ||
            strcmp(keymap_package(s), f->package)) {
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
          unchanged=f->baseline_valid&&keymap_same_controls(&f->baseline,&model);
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
    int ok = unchanged || (dir >= 0 && keymap_save(dir, pkg, runtime, &model));
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
