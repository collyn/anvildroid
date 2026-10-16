/* Scale only the full-Android presentation; leave reserved canvas and app task
 * geometry untouched. HWC retains original coordinates for input transport. */
static struct object *full_ui_owner(struct object *s) {
  if(!s)return 0;
  if(s->full_ui)return s;
  for(struct object *o=objects;o;o=o->next)
    if(o->surface==s&&o->parent&&o->parent->full_ui&&
       !strcmp(o->p->interface->name,"wl_subsurface"))return o->parent;
  return 0;
}
/* Aspect-preserving content box inside the window. Prefer full height so
 * the black bars appear only on the sides; the box stays centered. */
static void full_ui_box(struct object *s,int *fw,int *fh,int *ox,int *oy) {
  int w=s->full_w,h=s->full_h;
  int cw=display_projection.w?display_projection.w:s->full_canvas_w;
  int ch=display_projection.h?display_projection.h:s->full_canvas_h;
  if(w<1||h<1){w=1;h=1;}
  if(s->full_canvas_w>0&&s->full_canvas_h>0) {
    w=(int)((int64_t)h*cw/ch);
    if(w>s->full_w) {
      w=s->full_w;
      h=(int)((int64_t)w*ch/cw);
    }
  }
  *fw=w>0?w:1;*fh=h>0?h:1;
  *ox=(s->full_w-*fw)/2;*oy=(s->full_h-*fh)/2;
}
static int full_ui_scaled(struct object *s,int value,int axis,int ceil) {
  int src=display_projection.w?(axis?display_projection.h:display_projection.w):(axis?s->full_canvas_h:s->full_canvas_w);
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  int dst=axis?fh:fw;
  if(src<=0||dst<=0)return value;
  int64_t n=(int64_t)value*dst;
  return (int)(ceil?(n+src-1)/src:n/src);
}
static void full_ui_fit(struct object *s,int w,int h,int maximized) {
  if(s->full_canvas_w<=0||s->full_canvas_h<=0)return;
  if(s->full_w>0&&s->full_h>0&&
     ((w<1&&h<1)||(w==s->full_w&&h==s->full_h)))return;
  if(w<1||h<1){w=s->full_w?s->full_w:1000;h=s->full_h?s->full_h:700;}
  if(w>8192||h>8192)return;
  int cw=display_projection.w?display_projection.w:s->full_canvas_w;
  int ch=display_projection.h?display_projection.h:s->full_canvas_h;
  if(!maximized) {
    /* Windowed: the window hugs the aspect-correct content, no bars. */
    if((int64_t)w*ch>(int64_t)h*cw)
      w=(int)((int64_t)h*cw/ch);
    else h=(int)((int64_t)w*ch/cw);
  }
  s->full_w=w>0?w:1;s->full_h=h>0?h:1;
}
/* Full UI has no Android caption strip to cover. Keep host chrome outside
 * the canvas and include it in xdg window geometry/configure dimensions. */
static int full_ui_chrome(struct object *s) {
  return s->csd && s->csd->pieces[0].surface && !s->csd->fullscreen;
}
static void full_ui_geometry(struct object *s, union argument *a) {
  int chrome=full_ui_chrome(s);
  a[0].i=chrome?-CSD_BORDER:0;
  a[1].i=chrome?-CSD_HEADER:0;
  a[2].i=s->full_w+(chrome?2*CSD_BORDER:0);
  a[3].i=s->full_h+(chrome?CSD_HEADER+CSD_BORDER:0);
}
static void full_ui_clip(struct object *layer) {
  struct object *s=full_ui_owner(layer),*vp=layer?layer->viewport:0,*sub=0;
  if(!s||layer==s||!vp||!display_projection.w)return;
  for(struct object *o=objects;o;o=o->next)
    if(o->surface==layer&&o->parent==s&&!strcmp(o->p->interface->name,"wl_subsurface")){sub=o;break;}
  if(!sub)return;
  struct display_crop c;
  int result=display_crop_layer(sub->x,sub->y,vp->width,vp->height,vp->source_x,vp->source_y,vp->source_width,vp->source_height,&c);
  if(result<0) {
    union argument empty[3]={{.o=0},{.i=0},{.i=0}};wayland_send(layer->p,1,empty);layer->clip_hidden=1;return;
  }
  if(!result)return;
  if(layer->clip_hidden&&layer->attached_buffer) {
    union argument attach[3]={{.o=layer->attached_buffer},{.i=layer->attach_x},{.i=layer->attach_y}};
    wayland_send(layer->p,1,attach);layer->clip_hidden=0;
  }
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  union argument source[4]={{.i=c.sx},{.i=c.sy},{.i=c.sw},{.i=c.sh}};
  union argument dest[2]={{.i=full_ui_scaled(s,c.w,0,1)},{.i=full_ui_scaled(s,c.h,1,1)}};
  union argument position[2]={{.i=ox+full_ui_scaled(s,sub->x+c.left-display_projection.x,0,0)},
    {.i=oy+full_ui_scaled(s,sub->y+c.top-display_projection.y,1,0)}};
  wayland_send(vp->p,1,source);wayland_send(vp->p,2,dest);wayland_send(sub->p,1,position);
  layer->full_clip_x=c.left;layer->full_clip_y=c.top;
}
static void full_ui_apply(struct object *s) {
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  for(struct object *o=objects;o;o=o->next) {
    if(o->surface&&full_ui_owner(o->surface)==s&&!strcmp(o->p->interface->name,"wp_viewport")&&o->width>0&&o->height>0) {
      union argument a[2]={{.i=full_ui_scaled(s,o->width,0,1)},{.i=full_ui_scaled(s,o->height,1,1)}};
      if(o->surface==s){a[0].i=s->full_w;a[1].i=s->full_h;}
      wayland_send(o->p,2,a);
      wayland_send(o->surface->p,6,0);
    }
    if(o->parent==s&&!strcmp(o->p->interface->name,"wl_subsurface")) {
      union argument a[2]={{.i=full_ui_scaled(s,o->x-display_projection.x,0,0)+ox},{.i=full_ui_scaled(s,o->y-display_projection.y,1,0)+oy}};
      wayland_send(o->p,1,a);
    }
  }
  if(display_projection.w)for(struct object *o=objects;o;o=o->next)
    if(full_ui_owner(o)==s&&o!=s){full_ui_clip(o);wayland_send(o->p,6,0);}
  if(s->xdg) {
    union argument a[4];full_ui_geometry(s,a);
    wayland_send(s->xdg->p,3,a);
  }
  wayland_send(s->p,6,0);
  /* Track the frame with the new fitted extents immediately; the canvas
   * HWC commits sparsely, so waiting for its next commit leaves the CSD
   * pieces lagging behind a resize or maximize. */
  keymap_canvas_bounds(s);
  keymap_overlay_sync(s,s->bx,s->by,s->bw,s->bh);
  csd_sync(s, 0, 0, s->full_w > 0 ? s->full_w : 1,
           s->full_h > 0 ? s->full_h : 1);
}
static void full_ui_request(struct object *o,const char *cls,const char *method,
                             union argument *a,struct proxy **temporary) {
  if(!o)return;
  struct object *s=full_ui_owner(!strcmp(cls,"wl_surface")?o:o->surface);
  if(!s)return;
  if(!strcmp(cls,"wl_surface")&&!strcmp(method,"commit")) {
    if(o==s&&display_projection.w) {
      /* Stock HWC commits a child before sending its final set_position.
       * Reconcile crops using that final position before the parent latches
       * synchronized child state. Otherwise set_position undoes the crop. */
      for(struct object *layer=objects;layer;layer=layer->next)
        if(layer!=s&&full_ui_owner(layer)==s) {
          full_ui_clip(layer);wayland_send(layer->p,6,0);
        }
    } else full_ui_clip(o);
  }
  if(!strcmp(cls,"wp_viewport")&&!strcmp(method,"set_destination")&&a[0].i>0&&a[1].i>0) {
    if(o->surface==s){a[0].i=s->full_w;a[1].i=s->full_h;}
    else {a[0].i=full_ui_scaled(s,a[0].i,0,1);a[1].i=full_ui_scaled(s,a[1].i,1,1);}
  }
  if(!strcmp(cls,"wl_subsurface")&&!strcmp(method,"set_position")) {
    int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
    a[0].i=full_ui_scaled(s,a[0].i+(o->surface?o->surface->full_clip_x:0)-display_projection.x,0,0)+ox;
    a[1].i=full_ui_scaled(s,a[1].i+(o->surface?o->surface->full_clip_y:0)-display_projection.y,1,0)+oy;
  }
  if(!strcmp(cls,"xdg_surface")&&!strcmp(method,"set_window_geometry")) {
    full_ui_geometry(s,a);
  }
  if(!strcmp(cls,"wl_surface")&&!strcmp(method,"set_input_region")&&a[0].o&&csd_compositor&&csd_region_i) {
    struct object *r=find(a[0].o);
    if(r&&r->valid) {
      union argument id={.o=0};
      *temporary=real_marshal(csd_compositor,1,&id,csd_region_i,1);
      if(*temporary) {
        int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
        union argument rect[4]={{.i=full_ui_scaled(s,r->x,0,0)+ox},{.i=full_ui_scaled(s,r->y,1,0)+oy},
          {.i=full_ui_scaled(s,r->width,0,1)},{.i=full_ui_scaled(s,r->height,1,1)}};
        wayland_send(*temporary,1,rect);a[0].o=*temporary;
      }
    }
  }
}
static int32_t full_ui_input(struct proxy *surface,int32_t value,int axis) {
  struct object *layer=find(surface),*s=full_ui_owner(layer);
  if(!s)return value;
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  int src=display_projection.w?(axis?display_projection.h:display_projection.w):(axis?s->full_canvas_h:s->full_canvas_w),dst=axis?fh:fw;
  if(src<=0||dst<=0)return value;
  /* Only root coordinates include the letterbox origin. A child is already
   * positioned inside it, so subtracting it again shifts every click/scroll.
   * Wayland pointer coordinates are 24.8 fixed point; box offsets are pixels. */
  int64_t n=value;
  if(layer==s)n-=256LL*(axis?oy:ox);
  n=n*src/dst;
  n+=256LL*(layer==s?(axis?display_projection.y:display_projection.x):(axis?layer->full_clip_y:layer->full_clip_x));
  return n>INT32_MAX?INT32_MAX:n<INT32_MIN?INT32_MIN:(int32_t)n;
}
