/* Chrome owns the pointer only while it is over an editor/menu surface.
 * HWC may issue set_cursor(NULL) for unknown surfaces; override that request
 * with the same valid enter serial, never an invented pointer grab serial. */
static int csd_cursor_owned(struct object *o) {
  return o&&((o->overlay_focus&&o->overlay_focus->overlay)||
             (o->menu_focus&&o->menu_focus->menu_open));
}
static void csd_cursor_show(struct object *o) {
  if(!csd_cursor_owned(o)||!o->pointer_surface||!o->p)return;
  if(!o->edit_cursor) {
    if(!csd_compositor||!csd_surface_i||!csd_shm)return;
    union argument id={.o=0};
    struct proxy *surface=real_marshal(csd_compositor,0,&id,csd_surface_i,1);
    if(!surface)return;
    struct proxy *buffer=csd_paint(7,26,32,0);
    if(!buffer){wayland_send(surface,0,0);real_destroy(surface);return;}
    union argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
    union argument damage[4]={{.i=0},{.i=0},{.i=26},{.i=32}};
    wayland_send(surface,1,attach);wayland_send(surface,2,damage);
    wayland_send(surface,6,0);
    o->edit_cursor=surface;
  }
  union argument cursor[4]={{.u=o->cursor_serial},{.o=o->edit_cursor},{.i=3},{.i=2}};
  wayland_send(o->p,0,cursor);
}
static void csd_cursor_request(struct object *o,union argument *a) {
  if(!csd_cursor_owned(o)||!o->edit_cursor||!o->pointer_surface)return;
  a[0].u=o->cursor_serial;a[1].o=o->edit_cursor;a[2].i=3;a[3].i=2;
}
static void csd_cursor_destroy(struct object *o) {
  if(!o||!o->edit_cursor)return;
  wayland_send(o->edit_cursor,0,0);real_destroy(o->edit_cursor);o->edit_cursor=0;
}
