/* Bridge-owned relative pointer and one-shot lock. A request is not a grant:
 * Android camera input starts only after the compositor's locked event. All
 * callbacks and mutations run under the existing bridge mutex. */
struct keymap_capture_binding {
  struct wl_display *display;
  struct proxy *relative_manager,*constraints;
  uint32_t relative_name,constraints_name;
  struct keymap_capture_binding *next;
};
static struct keymap_capture_binding *keymap_capture_bindings;
static struct {
  struct object *owner,*pointer;
  struct proxy *relative,*locked;
  struct wl_display *display;
  struct proxy *surface;
  int active;
  long requested;
} keymap_capture;
static int keymap_capture_request_lock(struct proxy *surface);
static const struct interface keymap_relative_interface;
static const struct interface keymap_locked_interface;
static const struct interface keymap_confined_interface;
static const void *keymap_relative_types[]={&keymap_relative_interface,0};
static const void *keymap_lock_types[]={&keymap_locked_interface,0,0,0,0};
static const void *keymap_confine_types[]={&keymap_confined_interface,0,0,0,0};
static const void *keymap_region_types[]={0};
static const struct message keymap_relative_manager_methods[]={
  {"destroy","",0},{"get_relative_pointer","no",keymap_relative_types}};
static const struct interface keymap_relative_manager_interface={
  "zwp_relative_pointer_manager_v1",1,2,keymap_relative_manager_methods,0,0};
static const struct message keymap_relative_methods[]={{"destroy","",0}};
static const struct message keymap_relative_events[]={{"relative_motion","uuffff",0}};
static const struct interface keymap_relative_interface={
  "zwp_relative_pointer_v1",1,1,keymap_relative_methods,1,keymap_relative_events};
static const struct message keymap_constraints_methods[]={
  {"destroy","",0},{"lock_pointer","noo?ou",keymap_lock_types},
  {"confine_pointer","noo?ou",keymap_confine_types}};
static const struct interface keymap_constraints_interface={
  "zwp_pointer_constraints_v1",1,3,keymap_constraints_methods,0,0};
static const struct message keymap_locked_methods[]={
  {"destroy","",0},{"set_cursor_position_hint","ff",0},
  {"set_region","?o",keymap_region_types}};
static const struct message keymap_locked_events[]={{"locked","",0},{"unlocked","",0}};
static const struct interface keymap_locked_interface={
  "zwp_locked_pointer_v1",1,3,keymap_locked_methods,2,keymap_locked_events};
static const struct message keymap_confined_methods[]={
  {"destroy","",0},{"set_region","?o",keymap_region_types}};
static const struct message keymap_confined_events[]={{"confined","",0},{"unconfined","",0}};
static const struct interface keymap_confined_interface={
  "zwp_confined_pointer_v1",1,2,keymap_confined_methods,2,keymap_confined_events};
static void keymap_capture_protocol_init(void *library) {
  const void *pointer=dlsym(library,"wl_pointer_interface");
  const void *surface=dlsym(library,"wl_surface_interface");
  const void *region=dlsym(library,"wl_region_interface");
  keymap_relative_types[1]=pointer;
  keymap_lock_types[1]=keymap_confine_types[1]=surface;
  keymap_lock_types[2]=keymap_confine_types[2]=pointer;
  keymap_lock_types[3]=keymap_confine_types[3]=keymap_region_types[0]=region;
}
static struct keymap_capture_binding *keymap_capture_for(struct wl_display *display) {
  for(struct keymap_capture_binding *b=keymap_capture_bindings;b;b=b->next)
    if(b->display==display)return b;
  return 0;
}
static int keymap_capture_supported(struct object *owner) {
  struct keymap_capture_binding *b=owner?keymap_capture_for(owner->display):0;
  return b&&b->relative_manager&&b->constraints;
}
static int keymap_capture_owner_valid(void) {
  int owner=0,pointer=0,focus=0;
  for(struct object *o=objects;o;o=o->next) {
    if(o==keymap_capture.owner)owner=1;
    if(o==keymap_capture.pointer)pointer=1;
    if(o->keyboard_focus==keymap_capture.owner&&!o->keyboard_leave_pending&&!o->keymap_panel_focus)focus=1;
  }
  return owner&&pointer&&focus&&(!keymap_capture.owner->overlay||keymap_capture.owner->overlay->testing)&&
    !keymap_capture.owner->keymap_disabled&&!keymap_capture.owner->resize.dragging&&
    game_fit_owner(find(keymap_capture.pointer->pointer_surface))==keymap_capture.owner;
}
static void keymap_capture_stop(void) {
  struct object *pointer=keymap_capture.pointer;
  int restore=keymap_capture.active;
  struct proxy *locked=keymap_capture.locked,*relative=keymap_capture.relative;
  /* Clear before destroy: callbacks and cancellation may consult this state. */
  memset(&keymap_capture,0,sizeof(keymap_capture));
  if(locked){wayland_send(locked,0,0);real_destroy(locked);}
  if(relative){wayland_send(relative,0,0);real_destroy(relative);}
  if(restore)csd_cursor_restore(pointer);
}
static int keymap_capture_hides_cursor(struct object *pointer) {
  return keymap_capture.pointer==pointer&&keymap_capture.active;
}
static void keymap_capture_pause_owner(void) {
  for(struct object *o=objects;o;o=o->next)if(o==keymap_capture.owner)o->keymap_auto_paused=1;
}
static int keymap_capture_active(struct object *owner) {
  if(keymap_capture.owner!=owner||!keymap_capture.locked)return 0;
  long elapsed=map_monotonic_ms()-keymap_capture.requested;
  if(!keymap_capture_owner_valid()||(!keymap_capture.active&&elapsed>2000)) {
    __android_log_print(4,"AnvilKeymapCapture","capture cancelled: %s",
      keymap_capture_owner_valid()?"grant timed out":"focus lost");
    keymap_capture_pause_owner();
    scrcpy_mapping_cancel();return 0;
  }
  /* KWin grants constraints on the xdg window, even when wl_pointer.enter
   * names an Android content subsurface. Other compositors (including our
   * X11 backend) require that exact entered surface. Try the window first,
   * then its focused content once, within the original request deadline. */
  if(!keymap_capture.active&&elapsed>250&&keymap_capture.surface==owner->p&&
      keymap_capture.pointer->pointer_surface!=owner->p) {
    struct proxy *old=keymap_capture.locked;keymap_capture.locked=0;
    wayland_send(old,0,0);real_destroy(old);
    if(!keymap_capture_request_lock(keymap_capture.pointer->pointer_surface)) {
      keymap_capture_pause_owner();scrcpy_mapping_cancel();return 0;
    }
  }
  return keymap_capture.active;
}
static int __attribute__((unused)) keymap_capture_status(struct object *owner) {
  if(!keymap_capture_supported(owner))return 0;
  if(keymap_capture.owner==owner&&keymap_capture.locked)return keymap_capture.active?3:2;
  return 1;
}
static void keymap_relative_motion(void *data,struct proxy *p,uint32_t hi,uint32_t lo,
    int32_t dx,int32_t dy,int32_t unaccel_x,int32_t unaccel_y) {
  (void)data;(void)hi;(void)lo;(void)dx;(void)dy;
  pthread_mutex_lock(mutex);
  if(p==keymap_capture.relative&&keymap_capture_active(keymap_capture.owner))
    scrcpy_mapping_relative(keymap_capture.owner,unaccel_x,unaccel_y);
  pthread_mutex_unlock(mutex);
}
static void keymap_locked(void *data,struct proxy *p) {
  (void)data;
  pthread_mutex_lock(mutex);
  if(p==keymap_capture.locked&&keymap_capture_owner_valid()) {
    keymap_capture.active=1;
    __android_log_print(4,"AnvilKeymapCapture","pointer locked: %s",
      keymap_capture.surface==keymap_capture.owner->p?"window":"content");
    /* A bridge arrow is restorable even before HWC's first cursor request. */
    if(keymap_capture.pointer->cursor_requested||keymap_capture.pointer->cursor_fallback) {
      union argument a[4]={{.u=keymap_capture.pointer->cursor_serial},{.o=0},{.i=0},{.i=0}};
      wayland_send(keymap_capture.pointer->p,0,a);
    }
    scrcpy_mapping_relative(keymap_capture.owner,0,0);
  } else if(p==keymap_capture.locked)scrcpy_mapping_cancel();
  pthread_mutex_unlock(mutex);
}
static void keymap_unlocked(void *data,struct proxy *p) {
  (void)data;
  pthread_mutex_lock(mutex);
  if(p==keymap_capture.locked){
    keymap_capture_pause_owner();
    scrcpy_mapping_cancel();
  }
  pthread_mutex_unlock(mutex);
}
static void (*keymap_relative_listener[])(void)={(void (*)(void))keymap_relative_motion};
static void (*keymap_locked_listener[])(void)={(void (*)(void))keymap_locked,(void (*)(void))keymap_unlocked};
static int keymap_capture_request_lock(struct proxy *surface) {
  struct keymap_capture_binding *b=keymap_capture_for(keymap_capture.display);
  union argument lock[5]={{.o=0},{.o=surface},{.o=keymap_capture.pointer->p},{.o=0},{.u=1}};
  keymap_capture.surface=surface;
  keymap_capture.locked=real_marshal(b->constraints,1,lock,&keymap_locked_interface,1);
  if(!keymap_capture.locked)return 0;
  if(real_set_queue)real_set_queue(keymap_capture.locked,0);
  if(real_listen(keymap_capture.locked,keymap_locked_listener,0))return 0;
  if(real_display_flush&&real_display_flush(keymap_capture.display)<0&&PROFILE_ERRNO!=11)return 0;
  __android_log_print(4,"AnvilKeymapCapture","request pointer lock: %s",
    surface==keymap_capture.owner->p?"window":"content");
  return 1;
}
static int keymap_capture_start(struct object *owner) {
  if(!keymap_capture_supported(owner)||!real_marshal||!real_listen||!real_destroy||!get_version)return 0;
  if(keymap_capture.owner)return keymap_capture.owner==owner;
  struct object *pointer=0;
  for(struct object *o=objects;o;o=o->next)
    if(object_is(o,"wl_pointer")&&o->display==owner->display&&o->pointer_surface&&
       game_fit_owner(find(o->pointer_surface))==owner) {pointer=o;break;}
  if(!pointer)return 0;
  keymap_capture.owner=owner;keymap_capture.pointer=pointer;
  keymap_capture.display=owner->display;keymap_capture.requested=map_monotonic_ms();
  if(!keymap_capture_owner_valid()){keymap_capture_stop();return 0;}
  struct keymap_capture_binding *b=keymap_capture_for(owner->display);
  union argument relative[2]={{.o=0},{.o=pointer->p}};
  keymap_capture.relative=real_marshal(b->relative_manager,1,relative,&keymap_relative_interface,1);
  if(!keymap_capture.relative){keymap_capture_stop();return 0;}
  if(real_set_queue)real_set_queue(keymap_capture.relative,0);
  if(real_listen(keymap_capture.relative,keymap_relative_listener,0)) {keymap_capture_stop();return 0;}
  if(!keymap_capture_request_lock(owner->p)){keymap_capture_stop();return 0;}
  return 1;
}
static int keymap_capture_global(struct object *registry,uint32_t name,const char *interface,uint32_t version) {
  int relative=!strcmp(interface,"zwp_relative_pointer_manager_v1");
  int constraints=!strcmp(interface,"zwp_pointer_constraints_v1");
  if(!relative&&!constraints)return 0;
  if(version<1)return 1;
  pthread_mutex_lock(mutex);
  struct keymap_capture_binding *b=keymap_capture_for(registry->display);
  if(!b) {
    b=calloc(1,sizeof(*b));if(!b)abort();b->display=registry->display;
    b->next=keymap_capture_bindings;keymap_capture_bindings=b;
  }
  struct proxy **manager=relative?&b->relative_manager:&b->constraints;
  if(!*manager) {
    const struct interface *iface=relative?&keymap_relative_manager_interface:&keymap_constraints_interface;
    union argument a[4]={{.u=name},{.s=interface},{.u=1},{.o=0}};
    *manager=real_marshal(registry->p,0,a,iface,1);
    if(*manager) {
      if(real_set_queue)real_set_queue(*manager,0);
      if(relative)b->relative_name=name;else b->constraints_name=name;
    }
  }
  pthread_mutex_unlock(mutex);return 1;
}
static void keymap_capture_disconnect(struct wl_display *display) {
  if(keymap_capture.display==display)scrcpy_mapping_cancel();
  struct keymap_capture_binding **at=&keymap_capture_bindings;
  while(*at) {
    struct keymap_capture_binding *b=*at;
    if(b->display!=display){at=&b->next;continue;}
    if(b->relative_manager){wayland_send(b->relative_manager,0,0);real_destroy(b->relative_manager);}
    if(b->constraints){wayland_send(b->constraints,0,0);real_destroy(b->constraints);}
    *at=b->next;free(b);
  }
}
static int keymap_capture_remove(struct object *registry,uint32_t name) {
  pthread_mutex_lock(mutex);
  struct keymap_capture_binding *b=keymap_capture_for(registry->display);
  if(!b||(b->relative_name!=name&&b->constraints_name!=name)) {
    pthread_mutex_unlock(mutex);return 0;
  }
  if(keymap_capture.display==registry->display)scrcpy_mapping_cancel();
  struct proxy **manager=b->relative_name==name?&b->relative_manager:&b->constraints;
  if(*manager){wayland_send(*manager,0,0);real_destroy(*manager);*manager=0;}
  if(b->relative_name==name)b->relative_name=0;else b->constraints_name=0;
  pthread_mutex_unlock(mutex);return 1;
}
static void keymap_capture_forget(struct object *o) {
  if(o==keymap_capture.owner||o==keymap_capture.pointer ||
     (keymap_capture.pointer&&o->p==keymap_capture.pointer->pointer_surface))
    scrcpy_mapping_cancel();
  for(struct object *p=objects;p;p=p->next)if(p->cursor_surface==o->p) {
    p->cursor_surface=0;
    if(p!=o&&p->pointer_surface!=o->p)csd_cursor_show(p);
  }
}
static void keymap_capture_cursor_request(struct object *o,union argument *a) {
  if(!o)return;
  o->cursor_surface=a[1].o;o->cursor_hotspot_x=a[2].i;o->cursor_hotspot_y=a[3].i;o->cursor_requested=1;
  if(o==keymap_capture.pointer&&keymap_capture.active) {
    a[0].u=o->cursor_serial;a[1].o=0;a[2].i=a[3].i=0;
  }
}
