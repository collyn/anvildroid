/* Single-display window controls through the public Wayland decoration ABI. */
#include <stdint.h>
#include <stddef.h>
struct wl_proxy;
struct wl_interface;
struct wl_message { const char *name, *signature; const struct wl_interface **types; };
struct wl_interface {
  const char *name;
  int version, method_count;
  const struct wl_message *methods;
  int event_count;
  const struct wl_message *events;
};
union wl_argument { int32_t i; uint32_t u; const char *s; void *o; };
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern int __android_log_print(int, const char *, const char *, ...);
typedef struct wl_proxy *(*marshal_fn)(struct wl_proxy *, uint32_t,
    const struct wl_interface *, uint32_t, uint32_t, union wl_argument *);
static marshal_fn marshal;
static int (*listen)(struct wl_proxy *, void (**)(void), void *);
static const char *(*get_class)(struct wl_proxy *);
static uint32_t (*get_version)(struct wl_proxy *);
static struct wl_proxy *manager, *top, *decoration;
static const struct wl_interface *decoration_types[2];
static const struct wl_message decoration_methods[] = {
    {"destroy", "", NULL}, {"set_mode", "u", NULL}, {"unset_mode", "", NULL}};
static const struct wl_message decoration_events[] = {{"configure", "u", NULL}};
static const struct wl_interface decoration_interface = {
    "zxdg_toplevel_decoration_v1", 1, 3, decoration_methods, 1, decoration_events};
static const struct wl_message manager_methods[] = {
    {"destroy", "", NULL}, {"get_toplevel_decoration", "no", decoration_types}};
static const struct wl_interface manager_interface = {
    "zxdg_decoration_manager_v1", 1, 2, manager_methods, 0, NULL};
static void resolve(void) {
  if (marshal) return;
  void *library = dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so", 2 | 4);
  if (!library) return;
  listen = (void *)dlsym(library, "wl_proxy_add_listener");
  get_class = (void *)dlsym(library, "wl_proxy_get_class");
  get_version = (void *)dlsym(library, "wl_proxy_get_version");
  marshal = (void *)dlsym(library, "wl_proxy_marshal_array_flags");
}
static int enabled(void) {
  const char *value = getenv("ANVILDROID_ANDROID_WINDOW");
  return value && !strcmp(value, "1");
}

/* Public protocol objects only: preserve Android's canvas while presenting
 * its child surfaces in a letterboxed, independently sized host window. */
extern void *calloc(size_t, size_t);
extern void free(void *);
struct wl_array { size_t size, alloc; void *data; };
struct tracked {
  struct wl_proxy *proxy, *surface, *parent;
  int kind, x, y, width, height;
  struct tracked *next;
};
enum { SURFACE=1, XDG, VIEWPORT, SUBSURFACE };
static struct tracked *objects;
static struct wl_proxy *root_surface, *xdg;
static int host_w=1280, host_h=720, fit_w=1280, fit_h=720, pad_x, pad_y;
static const int canvas_w=1280, canvas_h=720;
static int fullscreen, focused, configured;
static int root_has_buffer;
static int fullscreen_requested=-1;
static int restore_w=1280, restore_h=720;
static void (*top_events[4])(void), (*top_original[4])(void);
static void (*xdg_events[1])(void), (*xdg_original[1])(void);
static void (*key_events[6])(void), (*key_original[6])(void);
static void (*pointer_events[11])(void), (*pointer_original[11])(void);
static void (*touch_events[7])(void), (*touch_original[7])(void);
static struct wl_proxy *pointer_surface;
static int pointer_inside=1;
static struct { int id, used; struct wl_proxy *surface; } touches[32];
static struct tracked *find(struct wl_proxy *p) {
  for (struct tracked *o=objects; o; o=o->next) if(o->proxy==p) return o;
  return NULL;
}
static int owned(struct wl_proxy *p) {
  if (!p || !root_surface) return 0;
  if (p==root_surface) return 1;
  for (struct tracked *o=objects;o;o=o->next)
    if(o->kind==SUBSURFACE && o->surface==p && o->parent==root_surface) return 1;
  return 0;
}
#include "android-ime.inc"
#include "android-csd.inc"

static void remember(struct wl_proxy *p,int kind,struct wl_proxy *s,struct wl_proxy *parent) {
  struct tracked *o=calloc(1,sizeof(*o));
  if(!o)return;
  o->proxy=p;o->kind=kind;o->surface=s;o->parent=parent;o->next=objects;objects=o;
}
static int scaled(int n,int axis) {
  return (int)((int64_t)n*(axis?fit_h:fit_w)/(axis?canvas_h:canvas_w));
}
static void fit(int w,int h) {
  if(w>0 && w<=32768)host_w=w;
  if(h>0 && h<=32768)host_h=h;
  fit_w=host_w;fit_h=host_h;
  if((int64_t)host_w*canvas_h>(int64_t)host_h*canvas_w)
    fit_w=(int)((int64_t)host_h*canvas_w/canvas_h);
  else fit_h=(int)((int64_t)host_w*canvas_h/canvas_w);
  if(fit_w<1)fit_w=1;
  if(fit_h<1)fit_h=1;
  pad_x=(host_w-fit_w)/2;pad_y=(host_h-fit_h)/2;
}
static void destination(struct tracked *o, union wl_argument *a) {
  if(o->surface==root_surface) {a[0].i=host_w;a[1].i=host_h;}
  else {a[0].i=scaled(o->width,0);a[1].i=scaled(o->height,1);}
  if(a[0].i<1)a[0].i=1;
  if(a[1].i<1)a[1].i=1;
}
static void apply_size(void) {
  if(!root_surface || !configured || !root_has_buffer)return;
  for(struct tracked *o=objects;o;o=o->next) {
    if(o->kind==VIEWPORT && owned(o->surface) && o->width>0 && o->height>0) {
      union wl_argument a[2];destination(o,a);
      marshal(o->proxy,2,NULL,1,0,a);
      marshal(o->surface,6,NULL,get_version(o->surface),0,NULL);
    } else if(o->kind==SUBSURFACE && o->parent==root_surface) {
      union wl_argument a[2]={{.i=pad_x+scaled(o->x,0)},{.i=pad_y+scaled(o->y,1)}};
      marshal(o->proxy,1,NULL,1,0,a);
    }
  }
  csd_sync();
  union wl_argument g[4];csd_geometry(g);
  if(xdg)marshal(xdg,3,NULL,get_version(xdg),0,g);
  marshal(root_surface,6,NULL,get_version(root_surface),0,NULL);
}
static void top_configure(void *data,struct wl_proxy *p,int32_t w,int32_t h,struct wl_array *states) {
  decoration_mode=decoration_pending;
  fullscreen_requested=-1;
  int was_maximized=maximized;
  int was_fullscreen=fullscreen;
  if(!was_maximized && !was_fullscreen){restore_w=host_w;restore_h=host_h;}
  fullscreen=0;maximized=0;
  if(states)for(size_t i=0;i<states->size/sizeof(uint32_t);i++)
    { if(((uint32_t*)states->data)[i]==2)fullscreen=1;
      if(((uint32_t*)states->data)[i]==1)maximized=1; }
  if(was_maximized!=maximized)csd_pieces[0].width=0;
  if(csd_visible()) {
    if(w>0)w=w>2*CSD_BORDER?w-2*CSD_BORDER:1;
    if(h>0)h=h>CSD_HEADER+CSD_BORDER?h-CSD_HEADER-CSD_BORDER:1;
  }
  if(!maximized && !fullscreen && (was_maximized || was_fullscreen)) {
    if(!w)w=restore_w;
    if(!h)h=restore_h;
  }
  fit(w,h);
  ((void(*)(void*,struct wl_proxy*,int32_t,int32_t,struct wl_array*))top_original[0])(data,p,canvas_w,canvas_h,states);
}
static void surface_configure(void *data,struct wl_proxy *p,uint32_t serial) {
  ((void(*)(void*,struct wl_proxy*,uint32_t))xdg_original[0])(data,p,serial);
  configured=1;
  apply_size();
}
static void keyboard_enter(void *d,struct wl_proxy *p,uint32_t serial,struct wl_proxy *s,struct wl_array *keys) {
  focused=owned(s);
  ((void(*)(void*,struct wl_proxy*,uint32_t,struct wl_proxy*,struct wl_array*))key_original[1])(d,p,serial,s,keys);
}
static void keyboard_leave(void *d,struct wl_proxy *p,uint32_t serial,struct wl_proxy *s) {
  focused=0;
  ((void(*)(void*,struct wl_proxy*,uint32_t,struct wl_proxy*))key_original[2])(d,p,serial,s);
}
static void keyboard_key(void *d,struct wl_proxy *p,uint32_t serial,uint32_t time,uint32_t key,uint32_t state) {
  if(focused && top && key==87) { /* Linux KEY_F11, not an XKB keycode. */
    if(state==1) {
      fullscreen_requested=!(fullscreen_requested<0?fullscreen:fullscreen_requested);
      union wl_argument output={.o=NULL};
      marshal(top,fullscreen_requested?11:12,NULL,get_version(top),0,fullscreen_requested?&output:NULL);
    }
    return;
  }
  ((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t,uint32_t,uint32_t))key_original[3])(d,p,serial,time,key,state);
}
static int32_t input(struct wl_proxy *s,int32_t n,int axis) {
  if(!owned(s))return n;
  int64_t v=n;
  if(s==root_surface)v-=(axis?pad_y:pad_x)*256LL;
  v=v*(axis?canvas_h:canvas_w)/(axis?fit_h:fit_w);
  if(v>INT32_MAX)v=INT32_MAX;
  if(v<INT32_MIN)v=INT32_MIN;
  return (int32_t)v;
}
static int inside(struct wl_proxy *s,int32_t x,int32_t y) {
  return s!=root_surface || (x>=pad_x*256 && y>=pad_y*256 && x<(pad_x+fit_w)*256 && y<(pad_y+fit_h)*256);
}
static void pointer_enter(void *d,struct wl_proxy *p,uint32_t serial,struct wl_proxy *s,int32_t x,int32_t y) {
  csd_part=csd_surface_part(s);csd_suppress=csd_part>=0;csd_pressed=0;
  csd_x=x/256;csd_y=y/256;
  pointer_surface=s;pointer_inside=inside(s,x,y);
  if(csd_suppress)return;
  ((void(*)(void*,struct wl_proxy*,uint32_t,struct wl_proxy*,int32_t,int32_t))pointer_original[0])(d,p,serial,s,input(s,x,0),input(s,y,1));
}
static void pointer_motion(void *d,struct wl_proxy *p,uint32_t time,int32_t x,int32_t y) {
  csd_x=x/256;csd_y=y/256;
  if(csd_suppress)return;
  pointer_inside=inside(pointer_surface,x,y);
  if(!pointer_inside)return;
  ((void(*)(void*,struct wl_proxy*,uint32_t,int32_t,int32_t))pointer_original[2])(d,p,time,input(pointer_surface,x,0),input(pointer_surface,y,1));
}
static void pointer_button(void *d,struct wl_proxy *p,uint32_t serial,uint32_t time,uint32_t button,uint32_t state) {
  if(csd_suppress){csd_button(serial,button,state);return;}
  if(!pointer_inside && state)return;
  ((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t,uint32_t,uint32_t))pointer_original[3])(d,p,serial,time,button,state);
}
static void pointer_leave(void *d,struct wl_proxy *p,uint32_t serial,struct wl_proxy *s) {
  pointer_surface=NULL;csd_part=-1;csd_pressed=0;
  if(!csd_suppress)((void(*)(void*,struct wl_proxy*,uint32_t,struct wl_proxy*))pointer_original[1])(d,p,serial,s);
}
static void pointer_axis(void *d,struct wl_proxy *p,uint32_t time,uint32_t axis,int32_t value) {
  if(!csd_suppress)((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t,int32_t))pointer_original[4])(d,p,time,axis,value);
}
static void pointer_frame(void *d,struct wl_proxy *p) {
  if(!csd_suppress && pointer_original[5])((void(*)(void*,struct wl_proxy*))pointer_original[5])(d,p);
}
static void pointer_source(void *d,struct wl_proxy *p,uint32_t source) {
  if(!csd_suppress && pointer_original[6])((void(*)(void*,struct wl_proxy*,uint32_t))pointer_original[6])(d,p,source);
}
static void pointer_stop(void *d,struct wl_proxy *p,uint32_t time,uint32_t axis) {
  if(!csd_suppress && pointer_original[7])((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t))pointer_original[7])(d,p,time,axis);
}
static void pointer_discrete(void *d,struct wl_proxy *p,uint32_t axis,int32_t value) {
  if(!csd_suppress && pointer_original[8])((void(*)(void*,struct wl_proxy*,uint32_t,int32_t))pointer_original[8])(d,p,axis,value);
}
static void pointer_value120(void *d,struct wl_proxy *p,uint32_t axis,int32_t value) {
  if(!csd_suppress && pointer_original[9])((void(*)(void*,struct wl_proxy*,uint32_t,int32_t))pointer_original[9])(d,p,axis,value);
}
static void pointer_direction(void *d,struct wl_proxy *p,uint32_t axis,uint32_t direction) {
  if(!csd_suppress && pointer_original[10])((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t))pointer_original[10])(d,p,axis,direction);
}
static void touch_down(void *d,struct wl_proxy *p,uint32_t serial,uint32_t time,struct wl_proxy *s,int32_t id,int32_t x,int32_t y) {
  if(csd_surface_part(s)>=0)return;
  if(!inside(s,x,y))return;
  for(int i=0;i<32;i++)if(!touches[i].used){touches[i].used=1;touches[i].id=id;touches[i].surface=s;break;}
  ((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t,struct wl_proxy*,int32_t,int32_t,int32_t))touch_original[0])(d,p,serial,time,s,id,input(s,x,0),input(s,y,1));
}
static void touch_up(void *d,struct wl_proxy *p,uint32_t serial,uint32_t time,int32_t id) {
  for(int i=0;i<32;i++)if(touches[i].used && touches[i].id==id) {
    touches[i].used=0;
    ((void(*)(void*,struct wl_proxy*,uint32_t,uint32_t,int32_t))touch_original[1])(d,p,serial,time,id);return;
  }
}
static void touch_motion(void *d,struct wl_proxy *p,uint32_t time,int32_t id,int32_t x,int32_t y) {
  for(int i=0;i<32;i++)if(touches[i].used && touches[i].id==id) {
    struct wl_proxy *s=touches[i].surface;
    ((void(*)(void*,struct wl_proxy*,uint32_t,int32_t,int32_t,int32_t))touch_original[2])(d,p,time,id,input(s,x,0),input(s,y,1));return;
  }
}
static void touch_cancel(void *d,struct wl_proxy *p) {
  for(int i=0;i<32;i++)touches[i].used=0;
  ((void(*)(void*,struct wl_proxy*))touch_original[4])(d,p);
}
static void copy_events(void (**to)(void),void (**original)(void),void (**from)(void),int n) {
  for(int i=0;i<n;i++)to[i]=original[i]=from[i];
}
static void decoration_configure(void *data, struct wl_proxy *proxy, uint32_t mode) {
  (void)data; (void)proxy;
  if(mode==1 || mode==2)decoration_pending=mode;
  __android_log_print(4, "AnvilWindow", "Android decoration mode=%u", mode);
}
static void (*decoration_listener[])(void) = {(void (*)(void))decoration_configure};
struct registry_listener {
  void (*global)(void *, struct wl_proxy *, uint32_t, const char *, uint32_t);
  void (*remove)(void *, struct wl_proxy *, uint32_t);
};
static const struct registry_listener *original_registry;
static void *registry_data;
static struct wl_proxy *registry_proxy;
static uint32_t manager_name;
static void global(void *data, struct wl_proxy *registry, uint32_t name,
                   const char *interface, uint32_t version) {
  (void)data;
  if (!manager && !strcmp(interface, manager_interface.name) && version >= 1) {
    union wl_argument args[] = {{.u=name}, {.s=manager_interface.name}, {.u=1}, {.o=NULL}};
    manager = marshal(registry, 0, &manager_interface, 1, 0, args);
    manager_name = name;
  }
  ime_global(registry,name,interface,version);
  original_registry->global(registry_data, registry, name, interface, version);
}
static void global_remove(void *data, struct wl_proxy *registry, uint32_t name) {
  (void)data;
  if(name==ime_manager_name){pthread_mutex_lock(mutex);ime_broken=1;ime_sync();pthread_mutex_unlock(mutex);}
  if (name == manager_name && manager) {
    marshal(manager, 0, NULL, 1, 1, NULL);
    manager = NULL;
  }
  if (original_registry->remove) original_registry->remove(registry_data, registry, name);
}
static const struct registry_listener registry_listener = {global, global_remove};
int wl_proxy_add_listener(struct wl_proxy *proxy, void (**implementation)(void), void *data) {
  resolve();
  if (!listen) return -1;
  if (enabled() && get_class && !registry_proxy && !strcmp(get_class(proxy), "wl_registry")) {
    original_registry = (const struct registry_listener *)implementation;
    registry_data = data;
    registry_proxy = proxy;
    return listen(proxy, (void (**)(void))&registry_listener, NULL);
  }

  if(enabled() && get_class) {
    const char *cls=get_class(proxy);
    if(proxy==top) {
      top_data=data;
      uint32_t v=get_version(proxy);
      copy_events(top_events,top_original,implementation,v>=5?4:v>=4?3:2);
      top_events[0]=(void(*)(void))top_configure;
      return listen(proxy,top_events,data);
    }
    if(proxy==xdg) {
      copy_events(xdg_events,xdg_original,implementation,1);
      xdg_events[0]=(void(*)(void))surface_configure;
      return listen(proxy,xdg_events,data);
    }
    if(!strcmp(cls,"wl_keyboard")) {
      copy_events(key_events,key_original,implementation,get_version(proxy)>=4?6:5);
      key_events[1]=(void(*)(void))keyboard_enter;key_events[2]=(void(*)(void))keyboard_leave;key_events[3]=(void(*)(void))keyboard_key;
      return listen(proxy,key_events,data);
    }
    if(!strcmp(cls,"wl_pointer") && get_version(proxy)<=9) {
      uint32_t v=get_version(proxy);
      copy_events(pointer_events,pointer_original,implementation,v>=9?11:v>=8?10:v>=5?9:5);
      pointer_events[0]=(void(*)(void))pointer_enter;pointer_events[2]=(void(*)(void))pointer_motion;pointer_events[3]=(void(*)(void))pointer_button;
      pointer_events[1]=(void(*)(void))pointer_leave;pointer_events[4]=(void(*)(void))pointer_axis;
      if(v>=5){pointer_events[5]=(void(*)(void))pointer_frame;pointer_events[6]=(void(*)(void))pointer_source;pointer_events[7]=(void(*)(void))pointer_stop;pointer_events[8]=(void(*)(void))pointer_discrete;}
      if(v>=8)pointer_events[9]=(void(*)(void))pointer_value120;
      if(v>=9)pointer_events[10]=(void(*)(void))pointer_direction;
      return listen(proxy,pointer_events,data);
    }
    if(!strcmp(cls,"wl_touch")) {
      copy_events(touch_events,touch_original,implementation,get_version(proxy)>=6?7:5);
      touch_events[0]=(void(*)(void))touch_down;touch_events[1]=(void(*)(void))touch_up;touch_events[2]=(void(*)(void))touch_motion;touch_events[4]=(void(*)(void))touch_cancel;
      return listen(proxy,touch_events,data);
    }
  }
  return listen(proxy, implementation, data);
}
struct wl_proxy *wl_proxy_marshal_array_flags(struct wl_proxy *proxy, uint32_t opcode,
    const struct wl_interface *interface, uint32_t version, uint32_t flags,
    union wl_argument *args) {
  resolve();
  if (!marshal) return NULL;
  if (!enabled()) return marshal(proxy,opcode,interface,version,flags,args);
  pthread_mutex_lock(mutex);ime_sync();pthread_mutex_unlock(mutex);
  struct tracked *o=find(proxy);
  union wl_argument adjusted[4];
  if (proxy==top && opcode==0) {
    csd_destroy();
    if(decoration)marshal(decoration,0,NULL,1,1,NULL);
    pthread_mutex_lock(mutex);ime_surface=NULL;ime_sync();pthread_mutex_unlock(mutex);
    decoration=NULL;top=NULL;root_surface=NULL;xdg=NULL;configured=0;focused=0;fullscreen=0;
    root_has_buffer=0;
    fullscreen_requested=-1;
    restore_w=1280;restore_h=720;fit(1280,720);
  }
  if(o && args) {
    if(o->kind==SURFACE && proxy==root_surface && opcode==1)root_has_buffer=args[0].o!=NULL;
    if(o->kind==VIEWPORT && opcode==2 && owned(o->surface) && args[0].i>0 && args[1].i>0) {
      o->width=args[0].i;o->height=args[1].i;destination(o,adjusted);args=adjusted;
    } else if(o->kind==SUBSURFACE && opcode==1 && o->parent==root_surface) {
      o->x=args[0].i;o->y=args[1].i;
      adjusted[0].i=pad_x+scaled(o->x,0);adjusted[1].i=pad_y+scaled(o->y,1);args=adjusted;
    } else if(o->kind==XDG && opcode==3 && proxy==xdg) {
      csd_geometry(adjusted);args=adjusted;
    } else if(o->kind==SURFACE && (opcode==4 || opcode==5) && owned(proxy)) {
      /* Full opaque/input region must cover the host viewport, not the
       * unscaled Android rectangle; the child surfaces receive app input. */
      adjusted[0].o=NULL;args=adjusted;
    }
  }
  if(proxy==root_surface && opcode==6 && configured && root_has_buffer) {
    apply_size();
    return NULL;
  }
  struct wl_proxy *result = marshal(proxy, opcode, interface, version, flags, args);
  if(result && interface) {
    csd_track(result,interface);
    if(!strcmp(interface->name,"wl_seat")&&!ime_seat){ime_seat=result;ime_create();}
    if(!strcmp(interface->name,"wl_surface"))remember(result,SURFACE,NULL,NULL);
    else if(!strcmp(interface->name,"xdg_surface")) {
      remember(result,XDG,args[1].o,NULL);xdg=result;root_surface=args[1].o;
    } else if(!strcmp(interface->name,"wp_viewport"))remember(result,VIEWPORT,args[1].o,NULL);
    else if(!strcmp(interface->name,"wl_subsurface"))remember(result,SUBSURFACE,args[1].o,args[2].o);
  }
  if (result && interface &&
      !strcmp(interface->name, "xdg_toplevel") && !top) {
    top = result;
    decoration_types[0] = &decoration_interface;
    decoration_types[1] = interface;
    union wl_argument create[] = {{.o=NULL}, {.o=top}};
    decoration = manager ? marshal(manager, 1, &decoration_interface, 1, 0, create) : NULL;
    if (decoration) {
      listen(decoration, decoration_listener, NULL);
      union wl_argument mode = {.u=2};
      marshal(decoration, 1, NULL, 1, 0, &mode);
    }
  }
  if (proxy == registry_proxy && flags & 1) registry_proxy = NULL;
  if(flags & 1) {
    struct tracked **entry=&objects;
    while(*entry) {
      if((*entry)->proxy==proxy) {struct tracked *old=*entry;*entry=old->next;free(old);break;}
      entry=&(*entry)->next;
    }
  }
  return result;
}
