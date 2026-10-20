/* A second real xdg_toplevel on HWC's existing connection. Only its own
 * roles/listeners are owned here; the borrowed wm_base keeps HWC's ping handler.
 * The Android overlay remains the canvas and sole owner of draft/history. */
static struct object *keymap_panel_owner(struct proxy *surface) {
  if(!surface)return 0;
  for(struct object *s=objects;s;s=s->next)
    if(s->overlay&&s->overlay->panel.piece.surface==surface)return s;
  return 0;
}
static int keymap_panel_header(const struct keymap_overlay *f) {return f->panel.decoration_mode==2?0:33;}
static void keymap_panel_destroy(struct keymap_overlay *f) {
  struct proxy *surface=f->panel.piece.surface;
  if(f->panel.decoration){wayland_send(f->panel.decoration,0,0);real_destroy(f->panel.decoration);}
  if(f->panel.top){wayland_send(f->panel.top,0,0);real_destroy(f->panel.top);}
  if(f->panel.xdg){wayland_send(f->panel.xdg,0,0);real_destroy(f->panel.xdg);}
  if(surface){wayland_send(surface,0,0);real_destroy(surface);}
  for(struct object *o=objects;o;o=o->next) {
    if(o->pointer_surface==surface&&surface){o->overlay_focus=0;o->overlay_pressed=0;o->csd_suppress=1;}
    if(o->keyboard_leave_surface==surface)o->keyboard_leave_surface=0;
    for(int i=0;i<32;++i)if(o->touch_surfaces[i]==surface)o->touch_surfaces[i]=0;
  }
  f->panel.piece=(struct csd_piece){0};f->panel.top=f->panel.xdg=0;
  f->panel.decoration=0;f->panel.decoration_mode=f->panel.decoration_pending=0;
  f->panel.configured=f->panel.dock_pending=0;f->detached=0;f->dirty=1;
}
static void keymap_panel_paint(uint32_t *pixels,int w,int h,struct keymap_overlay *f) {
  int header=keymap_panel_header(f);
  for(int y=0;y<h;++y)for(int x=0;x<w;++x)
    pixels[(size_t)y*w+x]=header&&y<32?0xff252b33:header&&y==32?0xff38404c:0xff19232f;
  if(header){keymap_text_scaled_clipped(pixels,w,h,w-38,12,12,"Keymap editor",0xffeef7ff,1);
    keymap_text_scaled(pixels,w,h,w-23,12,"X",0xffeef7ff,1);}
  if(h>header)keymap_editor_paint(pixels+(size_t)w*header,w,h-header,f);
}
static void keymap_panel_sync(struct keymap_overlay *f) {
  if(!f->detached||!f->panel.configured||!f->panel.piece.surface||!f->panel.dirty)return;
  int w=f->panel.width,h=f->panel.height;
  f->panel_paint=1;keymap_paint_context=f;
  struct proxy *buffer=csd_paint(4,w,h,0);
  keymap_paint_context=0;f->panel_paint=0;
  if(!buffer){f->panel.dock_pending=1;keymap_wake();return;}
  union argument geometry[4]={{.i=0},{.i=0},{.i=w},{.i=h}};
  wayland_send(f->panel.xdg,3,geometry);
  union argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
  wayland_send(f->panel.piece.surface,1,attach);
  union argument damage[4]={{.i=0},{.i=0},{.i=w},{.i=h}};
  wayland_send(f->panel.piece.surface,2,damage);
  csd_input(f->panel.piece.surface,0,0,w,h);
  wayland_send(f->panel.piece.surface,6,0);
  f->panel.piece.width=w;f->panel.piece.height=h;f->panel.dirty=0;
}
static void keymap_panel_configure(void *data,struct proxy *proxy,uint32_t serial) {
  pthread_mutex_lock(mutex);struct keymap_overlay *f=data;
  if(proxy==f->panel.xdg) {
    if(f->panel.decoration_pending)f->panel.decoration_mode=f->panel.decoration_pending;
    union argument ack={.u=serial};wayland_send(proxy,4,&ack);
    f->panel.configured=1;f->panel.dirty=1;
    keymap_panel_sync(f);keymap_wake();
  }
  pthread_mutex_unlock(mutex);
}
static void keymap_panel_size(void *data,struct proxy *proxy,int32_t w,int32_t h,void *states) {
  (void)states;pthread_mutex_lock(mutex);struct keymap_overlay *f=data;
  if(proxy==f->panel.top){
    if(w>=300&&w<=1024)f->panel.width=w;
    if(h>=453&&h<=1400)f->panel.height=h;
    f->panel.dirty=1;
  }
  pthread_mutex_unlock(mutex);
}
static void keymap_panel_close(void *data,struct proxy *proxy) {
  pthread_mutex_lock(mutex);struct keymap_overlay *f=data;
  if(proxy==f->panel.top){f->panel.dock_pending=1;f->dirty=1;keymap_wake();}
  pthread_mutex_unlock(mutex);
}
static void keymap_panel_bounds(void *data,struct proxy *proxy,int32_t w,int32_t h) {(void)data;(void)proxy;(void)w;(void)h;}
static void keymap_panel_capabilities(void *data,struct proxy *proxy,void *caps) {(void)data;(void)proxy;(void)caps;}
static void keymap_panel_decoration(void *data,struct proxy *proxy,uint32_t mode) {
  pthread_mutex_lock(mutex);struct keymap_overlay *f=data;
  if(proxy==f->panel.decoration&&(mode==1||mode==2))f->panel.decoration_pending=(int)mode;
  pthread_mutex_unlock(mutex);
}
static void (*keymap_panel_decoration_listener[])(void)={(void(*)(void))keymap_panel_decoration};
static void (*keymap_panel_surface_listener[])(void)={(void(*)(void))keymap_panel_configure};
static void (*keymap_panel_top_listener[])(void)={(void(*)(void))keymap_panel_size,
  (void(*)(void))keymap_panel_close,(void(*)(void))keymap_panel_bounds,(void(*)(void))keymap_panel_capabilities};
static int keymap_panel_create(struct object *s) {
  struct keymap_overlay *f=s->overlay;
  if(!f||f->detached||!csd_xdg_wm||!csd_xdg_surface_i||!csd_xdg_top_i||!csd_compositor||
     !csd_surface_i||!csd_shm||!real_marshal||!real_listen||!real_destroy||!get_version)return 0;
  f->panel.width=300;f->panel.height=737;
  union argument id={.o=0};
  f->panel.piece.surface=real_marshal(csd_compositor,0,&id,csd_surface_i,1);
  if(!f->panel.piece.surface)goto failed;
  if(real_set_queue)real_set_queue(f->panel.piece.surface,0);
  union argument a[2]={{.o=0},{.o=f->panel.piece.surface}};
  f->panel.xdg=real_marshal(csd_xdg_wm,2,a,csd_xdg_surface_i,get_version(csd_xdg_wm));
  if(!f->panel.xdg)goto failed;
  if(real_set_queue)real_set_queue(f->panel.xdg,0);
  if(real_listen(f->panel.xdg,keymap_panel_surface_listener,f))goto failed;
  f->panel.top=real_marshal(f->panel.xdg,1,&id,csd_xdg_top_i,get_version(f->panel.xdg));
  if(!f->panel.top)goto failed;
  if(real_set_queue)real_set_queue(f->panel.top,0);
  if(real_listen(f->panel.top,keymap_panel_top_listener,f))goto failed;
  struct decoration_binding *binding=decoration_for(s->display);
  if(binding){
    decoration_types[1]=csd_xdg_top_i;
    union argument args[2]={{.o=0},{.o=f->panel.top}};
    f->panel.decoration=real_marshal(binding->manager,1,args,&decoration_interface,1);
    if(!f->panel.decoration)goto failed;
    if(real_set_queue)real_set_queue(f->panel.decoration,0);
    if(real_listen(f->panel.decoration,keymap_panel_decoration_listener,f))goto failed;
    union argument mode={.u=1};wayland_send(f->panel.decoration,1,&mode);
  }
  char identity[512],title[320];const char *runtime=getenv("ANVILDROID_RUNTIME_ID");
  snprintf(identity,sizeof(identity),"waydroid.anvildroid.%s.%s.keymap-editor",runtime&&*runtime?runtime:"default",f->package);
  snprintf(title,sizeof(title),"Keymap editor - %s",f->package);
  union argument text={.s=identity};wayland_send(f->panel.top,3,&text);
  text.s=title;wayland_send(f->panel.top,2,&text);
  /* Keep the tools independently focusable. Weston redirects activation of
   * a transient's parent back to the tools, preventing canvas Try input.
   * The Android overlay still owns this window's draft and destruction. */
  union argument minimum[2]={{.i=300},{.i=453}};wayland_send(f->panel.top,8,minimum);
  f->detached=1;f->panel.dirty=1;f->dirty=1;f->dragging=0;
  /* Configure/ack is mandatory before the first non-NULL buffer commit. */
  wayland_send(f->panel.piece.surface,6,0);keymap_wake();return 1;
failed:
  keymap_panel_destroy(f);return 0;
}
static int keymap_panel_button(struct object *pointer,uint32_t serial,uint32_t button,uint32_t state) {
  struct object *s=keymap_panel_owner(pointer->pointer_surface);
  if(!s||pointer->csd_y>=keymap_panel_header(s->overlay))return 0;
  struct keymap_overlay *f=s->overlay;
  if(button==272&&state&&pointer->csd_y>=0) {
    if(pointer->csd_x>=f->panel.width-36){f->panel.dock_pending=1;f->dirty=1;keymap_wake();}
    else if(pointer->parent){union argument move[2]={{.o=pointer->parent->p},{.u=serial}};wayland_send(f->panel.top,5,move);}
  }
  return 1;
}
