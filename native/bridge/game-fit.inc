/* Compatibility presentation for the explicitly tested game. No Android task
 * resize, display resize or guessed supported resolution. Source bounds come
 * from the game's task; host windows use a separate contain transform.
 * Real Wayland child buffers remain owned by HWC (no screenshots/copies).
 */
#ifndef ANVIL_GAME_FIT
#define ANVIL_GAME_FIT 0
#endif
struct game_fit_state {
  struct csd_piece bars[4];
  struct proxy *viewports[4];
  int applied, x, y, sw, sh, w, h, csd, fullscreen;
  struct proxy *above;
};
static int game_fit_enabled(struct object *s) {
  return ANVIL_GAME_FIT && scale_has_viewporter && s && s->app_id &&
    !strcmp(s->app_id,"com.shg.sg432");
}
static struct object *game_fit_owner(struct object *s) {
  if(!s)return 0;
  if(game_fit_enabled(s))return s;
  for(struct object *o=objects;o;o=o->next)
    if(o->surface==s&&o->parent&&game_fit_enabled(o->parent)&&
       !strcmp(o->p->interface->name,"wl_subsurface"))return o->parent;
  return 0;
}
static void game_fit_report_transport(struct object *layer) {
  struct object *s=game_fit_owner(layer);
  /* The HWC parent and our four solid bars legitimately use tiny SHM buffers.
   * Only report Android content children, never mistake chrome for game pixels.
   * Transport is evidence about buffer import, not the game's GL renderer. */
  if(!s||s==layer||!layer->attached)return;
  if(layer->transport_seen&&layer->transport_reported==layer->buffer_transport)return;
  const char *names[]={"unknown","shm","dmabuf","native-handle"};
  __android_log_print(layer->buffer_transport==1?5:4,"AnvilGameFit",
    "buffer app=%s layer=%p transport=%s previous=%s renderer=unverified",
    s->app_id,(void *)layer->p,names[layer->buffer_transport],
    layer->transport_seen?names[layer->transport_reported]:"unobserved");
  layer->transport_seen=1;layer->transport_reported=layer->buffer_transport;
}
static struct game_fit_rect game_fit_area(struct object *s) {
  return game_fit_rect(s->fit_sw,s->fit_sh,s->fit_w,s->fit_h);
}
static int game_fit_scaled(struct object *s,int value,int axis) {
  struct game_fit_rect r=game_fit_area(s);
  int src=axis?s->fit_sh:s->fit_sw,dst=axis?r.height:r.width;
  if(src<=0||dst<=0)return value;
  int64_t n=(int64_t)value*dst,q=n/src;
  if(n<0&&n%src)--q;
  return (int)q;
}
/* Damage is surface-local, unlike damage_buffer. Round outwards so a scaled
 * partial update repaints every affected pixel (including negative origins).
 * Full-buffer damage must remain bounded even for HWC's INT32_MAX sentinel. */
static void game_fit_damage(struct object *s,union argument *a) {
  struct game_fit_rect r=game_fit_area(s);
  for(int axis=0;axis<2;++axis) {
    int src=axis?s->fit_sh:s->fit_sw,dst=axis?r.height:r.width;
    if(src<1||dst<1||a[axis+2].i<=0)continue;
    int64_t start=(int64_t)a[axis].i*dst;
    int64_t end=((int64_t)a[axis].i+a[axis+2].i)*dst;
    int64_t lo=start/src-(start<0&&start%src!=0);
    int64_t hi=end/src+(end>0&&end%src!=0);
    if(lo<INT32_MIN)lo=INT32_MIN;
    if(lo>INT32_MAX)lo=INT32_MAX;
    int64_t size=hi-lo;
    a[axis].i=(int32_t)lo;
    a[axis+2].i=size>INT32_MAX?INT32_MAX:size<0?0:(int32_t)size;
  }
}
static int32_t game_fit_input(struct proxy *p,int32_t value,int axis) {
  struct object *surface=find(p),*s=game_fit_owner(surface);
  if(!s||s->fit_sw<1||s->fit_sh<1)return value;
  struct game_fit_rect r=game_fit_area(s);
  int src=axis?s->fit_sh:s->fit_sw,dst=axis?r.height:r.width;
  if(dst<1)return value;
  /* Events on child surfaces are local to the scaled child. HWC still adds
   * its original layer position; do not add the letterbox origin twice. */
  int64_t n=value;
  if(surface==s)n-=256LL*(axis?r.y:r.x);
  n=n*src/dst;
  if(surface==s)n+=256LL*(axis?s->fit_y:s->fit_x);
  return n>INT32_MAX?INT32_MAX:n<INT32_MIN?INT32_MIN:(int32_t)n;
}
static int game_fit_bar(struct proxy *p) {
  for(struct object *s=objects;s;s=s->next)if(s->fit) {
    for(int i=0;i<4;++i)if(s->fit->bars[i].surface==p)return 1;
  }
  return 0;
}
static void game_fit_destroy(struct object *s) {
  if(!s||!s->fit)return;
  struct game_fit_state *f=s->fit;s->fit=0;
  for(int i=0;i<4;++i) {
    if(f->viewports[i]){wayland_send(f->viewports[i],0,0);real_destroy(f->viewports[i]);}
    if(f->bars[i].subsurface){wayland_send(f->bars[i].subsurface,0,0);real_destroy(f->bars[i].subsurface);}
    if(f->bars[i].surface){wayland_send(f->bars[i].surface,0,0);real_destroy(f->bars[i].surface);}
  }
  free(f);
}
static int game_fit_create(struct object *s) {
  if(s->fit)return 1;
  struct object *porter=0;
  for(struct object *o=objects;o;o=o->next)
    if(!strcmp(o->p->interface->name,"wp_viewporter")){porter=o;break;}
  if(!porter||!csd_compositor||!csd_subcompositor||!csd_shm)return 0;
  s->fit=calloc(1,sizeof(*s->fit));if(!s->fit)return 0;
  for(int i=0;i<4;++i) {
    union argument id={.o=0};
    struct csd_piece *b=&s->fit->bars[i];
    b->surface=real_marshal(csd_compositor,0,&id,csd_surface_i,1);
    if(!b->surface)goto failed;
    union argument sub[3]={{.o=0},{.o=b->surface},{.o=s->p}};
    b->subsurface=real_marshal(csd_subcompositor,1,sub,csd_subsurface_i,1);
    if(!b->subsurface)goto failed;
    union argument vp[2]={{.o=0},{.o=b->surface}};
    const struct interface *vi=porter->p->interface->methods[1].types[0];
    s->fit->viewports[i]=real_marshal(porter->p,1,vp,vi,1);
    if(!s->fit->viewports[i])goto failed;
  }
  return 1;
failed:
  game_fit_destroy(s);return 0;
}
static void game_fit_request(struct object *o,const char *cls,const char *method,
                             union argument *a,struct proxy **temporary) {
  if(!o)return;
  struct object *s=game_fit_owner(!strcmp(cls,"wl_surface")?o:o->surface);
  if(!s||!s->fit||s->fit_sw<1)return;
  struct game_fit_rect r=game_fit_area(s);
  if(!strcmp(cls,"wl_surface")&&!strcmp(method,"damage")&&o!=s)
    game_fit_damage(s,a);
  if(!strcmp(cls,"wp_viewport")&&!strcmp(method,"set_destination")&&a[0].i>0&&a[1].i>0) {
    if(o->surface==s){a[0].i=s->fit_w;a[1].i=s->fit_h;}
    else {
      a[0].i=game_fit_scaled(s,a[0].i,0);a[1].i=game_fit_scaled(s,a[1].i,1);
      if(a[0].i<1)a[0].i=1;
      if(a[1].i<1)a[1].i=1;
    }
  }
  if(!strcmp(cls,"wl_subsurface")&&!strcmp(method,"set_position")) {
    a[0].i=r.x+game_fit_scaled(s,a[0].i-s->fit_x,0);
    a[1].i=r.y+game_fit_scaled(s,a[1].i-s->fit_y,1);
  }
  /* HWC may replace input regions without changing geometry. Transform the
   * request itself so settled frames need not rebuild regions or recommit. */
  if(!strcmp(cls,"wl_surface")&&!strcmp(method,"set_input_region")) {
    int px=0,py=0;
    if(o!=s)for(struct object *sub=objects;sub;sub=sub->next)
      if(sub->surface==o&&sub->parent==s&&!strcmp(sub->p->interface->name,"wl_subsurface")) {
        px=r.x+game_fit_scaled(s,sub->x-s->fit_x,0);
        py=r.y+game_fit_scaled(s,sub->y-s->fit_y,1);break;
      }
    if(csd_compositor&&csd_region_i) {
      union argument id={.o=0};
      *temporary=real_marshal(csd_compositor,1,&id,csd_region_i,1);
      if(*temporary) {
        union argument rect[4]={{.i=o==s?0:r.x-px},{.i=o==s?0:r.y-py},
          {.i=o==s?s->fit_w:r.width},{.i=o==s?s->fit_h:r.height}};
        wayland_send(*temporary,1,rect);a[0].o=*temporary;
      }
    }
  }
}
static int game_fit_geometry(struct object *s,int x,int y,int sw,int sh) {
  if(!game_fit_enabled(s)||!s->viewport||sw<1||sh<1||sw>8192||sh>8192)return 0;
  if(!game_fit_create(s))return 0;
  s->fit_x=x;s->fit_y=y;s->fit_sw=sw;s->fit_sh=sh;
  if(s->fit_w<1||s->fit_h<1){s->fit_w=sw;s->fit_h=sh;}
  struct game_fit_rect r=game_fit_area(s);
  s->preview_width=s->preview_height=0;s->resize.width=s->resize.height=0;
  s->resize.ready=1;
  struct game_fit_state *f=s->fit;
  int csd=s->csd&&!decoration_available,fullscreen=s->csd&&s->csd->fullscreen;
  int changed=!f->applied||f->x!=x||f->y!=y||f->sw!=sw||f->sh!=sh||
    f->w!=s->fit_w||f->h!=s->fit_h||f->csd!=csd||f->fullscreen!=fullscreen;
  if(changed){
    __android_log_print(4,"AnvilGameFit",
      "presentation app=%s source=%dx%d host=%dx%d content=%d,%d %dx%d task_resize=0",
      s->app_id,sw,sh,s->fit_w,s->fit_h,r.x,r.y,r.width,r.height);
    union argument dest[2]={{.i=s->fit_w},{.i=s->fit_h}};
    wayland_send(s->viewport->p,2,dest);
  }
  struct proxy *above=0;
  for(struct object *sub=objects;sub;sub=sub->next) {
    if(sub->parent!=s||!sub->surface||strcmp(sub->p->interface->name,"wl_subsurface"))continue;
    if(!above)above=sub->surface->p;
    struct object *vp=sub->surface->viewport;
    if(!vp||vp->width<1||vp->height<1)continue;
    if(!changed&&sub->fit_applied&&sub->fit_last_x==sub->x&&sub->fit_last_y==sub->y&&
       sub->fit_last_w==vp->width&&sub->fit_last_h==vp->height)continue;
    int px=r.x+game_fit_scaled(s,sub->x-x,0),py=r.y+game_fit_scaled(s,sub->y-y,1);
    union argument pos[2]={{.i=px},{.i=py}};
    wayland_send(sub->p,1,pos);
    int dw=game_fit_scaled(s,vp->width,0),dh=game_fit_scaled(s,vp->height,1);
    union argument d[2]={{.i=dw>0?dw:1},{.i=dh>0?dh:1}};
    wayland_send(vp->p,2,d);
    csd_input(sub->surface->p,r.x-px,r.y-py,r.width,r.height);
    wayland_send(sub->surface->p,6,0);
    sub->fit_applied=1;sub->fit_last_x=sub->x;sub->fit_last_y=sub->y;
    sub->fit_last_w=vp->width;sub->fit_last_h=vp->height;
  }
  /* Opaque bars cover both empty space and any Android shadows outside the
   * source task. 1x1 buffers + viewports keep allocation constant while dragging. */
  struct game_fit_rect bars[4]={{0,0,s->fit_w,r.y},
    {0,r.y+r.height,s->fit_w,s->fit_h-r.y-r.height},
    {0,r.y,r.x,r.height},{r.x+r.width,r.y,s->fit_w-r.x-r.width,r.height}};
  int painted=1;
  if(changed||above!=f->above)for(int i=0;i<4;++i) {
    struct csd_piece *b=&s->fit->bars[i];struct game_fit_rect q=bars[i];
    union argument pos[2]={{.i=q.x},{.i=q.y}};wayland_send(b->subsurface,1,pos);
    if(above){union argument sibling={.o=above};wayland_send(b->subsurface,2,&sibling);}
    if(q.width>0&&q.height>0) {
      if(!b->width||!b->height){
        struct proxy *buffer=csd_paint(5,1,1,0);if(!buffer){painted=0;continue;}
        union argument a[3]={{.o=buffer},{.i=0},{.i=0}};wayland_send(b->surface,1,a);
      }
      union argument d[2]={{.i=q.width},{.i=q.height}};wayland_send(s->fit->viewports[i],2,d);
      union argument damage[4]={{.i=0},{.i=0},{.i=q.width},{.i=q.height}};wayland_send(b->surface,2,damage);
    }else{union argument a[3]={{.o=0},{.i=0},{.i=0}};wayland_send(b->surface,1,a);}
    b->width=q.width;b->height=q.height;wayland_send(b->surface,6,0);
  }
  if(changed||above!=f->above)csd_sync(s,0,0,s->fit_w,s->fit_h);
  keymap_overlay_sync(s,r.x,r.y,r.width,r.height);
  union argument g[4]={{.i=0},{.i=0},{.i=s->fit_w},{.i=s->fit_h}};
  if(s->csd&&!decoration_available&&!s->csd->fullscreen){
    g[0].i=-CSD_BORDER;g[1].i=-CSD_HEADER;
    g[2].i+=2*CSD_BORDER;g[3].i+=CSD_HEADER+CSD_BORDER;
  }
  if(changed)wayland_send(s->xdg->p,3,g);
  f->applied=painted;f->x=x;f->y=y;f->sw=sw;f->sh=sh;f->w=s->fit_w;f->h=s->fit_h;
  f->csd=csd;f->fullscreen=fullscreen;f->above=above;
  s->bx=0;s->by=0;s->bw=s->fit_w;s->bh=s->fit_h;s->has_bounds=1;
  return 1;
}
