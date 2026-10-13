/* Tokens originate in the GUI's Wayland client. They are used once, only on
 * this display's full Android surface. The compositor decides whether to focus. */
struct window_activation_binding {struct wl_display *display;struct proxy *manager;uint32_t name;struct window_activation_binding *next;};
static struct window_activation_binding *window_activations;
static const struct interface window_activation_token_interface={"xdg_activation_token_v1",1,0,0,0,0};
static const void *window_activation_new_types[]={&window_activation_token_interface};
static const void *window_activation_surface_types[]={0,0};
static const struct message window_activation_requests[]={
 {"destroy","",0},{"get_activation_token","n",window_activation_new_types},{"activate","so",window_activation_surface_types}};
static const struct interface window_activation_interface={"xdg_activation_v1",1,3,window_activation_requests,0,0};
static void window_activation_protocol_init(void *library){window_activation_surface_types[1]=dlsym(library,"wl_surface_interface");}
static void window_activation_disconnect(struct wl_display *display){
 struct window_activation_binding **at=&window_activations;
 while(*at){struct window_activation_binding *b=*at;if(b->display!=display){at=&b->next;continue;}
  wayland_send(b->manager,0,0);real_destroy(b->manager);*at=b->next;free(b);
 }
}
static void window_activation_global(struct object *registry,uint32_t name,uint32_t version){
 if(version<1)return;
 pthread_mutex_lock(mutex);
 for(struct window_activation_binding *b=window_activations;b;b=b->next)if(b->display==registry->display){pthread_mutex_unlock(mutex);return;}
 union argument a[4]={{.u=name},{.s="xdg_activation_v1"},{.u=1},{.o=0}};
 struct proxy *manager=real_marshal(registry->p,0,a,&window_activation_interface,1);
 if(manager){
  if(real_set_queue)real_set_queue(manager,0);
  struct window_activation_binding *b=calloc(1,sizeof(*b));if(!b)abort();
  b->display=registry->display;b->manager=manager;b->name=name;b->next=window_activations;window_activations=b;
 }
 pthread_mutex_unlock(mutex);
}
static int window_activation_remove(struct object *registry,uint32_t name){
 pthread_mutex_lock(mutex);
 for(struct window_activation_binding *b=window_activations;b;b=b->next)if(b->display==registry->display&&b->name==name){
  window_activation_disconnect(b->display);pthread_mutex_unlock(mutex);return 1;
 }
 pthread_mutex_unlock(mutex);return 0;
}
static int window_activate(const char *token){
 for(struct object *top=objects;top;top=top->next){
  if(!object_is(top,"xdg_toplevel")||!top->surface||
    !top->surface->window_configured||!top->surface->app_id||strcmp(top->surface->app_id,"Waydroid"))continue;
  if(top->display&&real_display_get_error&&real_display_get_error(top->display))continue;
  for(struct window_activation_binding *b=window_activations;b;b=b->next)if(b->display==top->display){
   union argument a[2]={{.s=token},{.o=top->surface->p}};wayland_send(b->manager,2,a);
   if(real_display_flush)real_display_flush(b->display);
   return 1;
  }
 }
 return 0;
}
