/* HWC can hide the compositor cursor before Android has drawn its own. Keep
 * normal app hover usable with a SHM arrow, but preserve real guest shapes,
 * intentional cursor subsurfaces and active relative-pointer capture. */
static int csd_cursor_owned(struct object *o) {
  return o&&((o->overlay_focus&&o->overlay_focus->overlay)||
             (o->menu_focus&&o->menu_focus->menu_open));
}
static int csd_cursor_subsurface=-1;
static int csd_cursor_content(struct object *o) {
  if(!keymap_owner(o->pointer_surface))return 0;
  if(csd_cursor_subsurface<0) {
    char value[92]="";
    if(__system_property_get)__system_property_get("persist.waydroid.cursor_on_subsurface",value);
    csd_cursor_subsurface=!strcmp(value,"true")||!strcmp(value,"1")||
      !strcmp(value,"yes")||!strcmp(value,"y")||!strcmp(value,"on");
  }
  return !csd_cursor_subsurface;
}
static int csd_cursor_create(struct object *o) {
  if(!o->edit_cursor) {
    if(!csd_compositor||!csd_surface_i||!csd_shm)return 0;
    union argument id={.o=0};
    struct proxy *surface=real_marshal(csd_compositor,0,&id,csd_surface_i,1);
    if(!surface)return 0;
    struct proxy *buffer=csd_paint(7,26,32,0);
    if(!buffer){wayland_send(surface,0,0);real_destroy(surface);return 0;}
    union argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
    union argument damage[4]={{.i=0},{.i=0},{.i=26},{.i=32}};
    wayland_send(surface,1,attach);wayland_send(surface,2,damage);
    wayland_send(surface,6,0);
    o->edit_cursor=surface;
  }
  return 1;
}
static void csd_cursor_request(struct object *o,union argument *a) {
  if(!o)return;
  o->cursor_fallback=0;
  if(!o->pointer_surface||!o->p||keymap_capture_hides_cursor(o))return;
  struct object *guest=find(o->cursor_surface);
  int missing=!o->cursor_surface||(guest&&!guest->committed_attached);
  if(!csd_cursor_owned(o)&&!(missing&&csd_cursor_content(o)))return;
  if(!csd_cursor_create(o))return;
  a[0].u=o->cursor_serial;a[1].o=o->edit_cursor;a[2].i=3;a[3].i=2;
  o->cursor_fallback=1;
}
static void csd_cursor_show(struct object *o) {
  union argument a[4]={{.u=0},{.o=0},{.i=0},{.i=0}};
  csd_cursor_request(o,a);
  if(o&&o->cursor_fallback)wayland_send(o->p,0,a);
}
static void csd_cursor_restore(struct object *o) {
  if(!o||!o->p||!o->pointer_surface||keymap_capture_hides_cursor(o))return;
  union argument a[4]={{.u=o->cursor_serial},{.o=o->cursor_surface},
    {.i=o->cursor_hotspot_x},{.i=o->cursor_hotspot_y}};
  csd_cursor_request(o,a);
  if(o->cursor_requested||o->cursor_fallback)wayland_send(o->p,0,a);
}
static void csd_cursor_commit(struct object *surface) {
  /* wl_buffer.destroy does not remove committed pixels. Only a subsequent
   * committed attach(NULL) makes a previously drawable cursor empty. */
  if(surface->committed_attached==surface->attached)return;
  surface->committed_attached=surface->attached;
  for(struct object *o=objects;o;o=o->next)if(o->cursor_surface==surface->p&&
      o->pointer_surface&&!keymap_capture_hides_cursor(o)) {
    int fallback=o->cursor_fallback;
    union argument a[4]={{.u=o->cursor_serial},{.o=o->cursor_surface},
      {.i=o->cursor_hotspot_x},{.i=o->cursor_hotspot_y}};
    csd_cursor_request(o,a);
    if(fallback!=o->cursor_fallback)wayland_send(o->p,0,a);
  }
}
static void csd_cursor_destroy(struct object *o) {
  if(!o||!o->edit_cursor)return;
  wayland_send(o->edit_cursor,0,0);real_destroy(o->edit_cursor);o->edit_cursor=0;
}
