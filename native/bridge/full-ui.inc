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
  if(w<1||h<1){w=1;h=1;}
  if(s->full_canvas_w>0&&s->full_canvas_h>0) {
    w=(int)((int64_t)h*s->full_canvas_w/s->full_canvas_h);
    if(w>s->full_w) {
      w=s->full_w;
      h=(int)((int64_t)w*s->full_canvas_h/s->full_canvas_w);
    }
  }
  *fw=w>0?w:1;*fh=h>0?h:1;
  *ox=(s->full_w-*fw)/2;*oy=(s->full_h-*fh)/2;
}
static int full_ui_scaled(struct object *s,int value,int axis,int ceil) {
  int src=axis?s->full_canvas_h:s->full_canvas_w;
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
  if(!maximized) {
    /* Windowed: the window hugs the aspect-correct content, no bars. */
    if((int64_t)w*s->full_canvas_h>(int64_t)h*s->full_canvas_w)
      w=(int)((int64_t)h*s->full_canvas_w/s->full_canvas_h);
    else h=(int)((int64_t)w*s->full_canvas_h/s->full_canvas_w);
  }
  s->full_w=w>0?w:1;s->full_h=h>0?h:1;
}
static void full_ui_apply(struct object *s) {
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  for(struct object *o=objects;o;o=o->next) {
    if(o->surface&&full_ui_owner(o->surface)==s&&!strcmp(o->p->interface->name,"wp_viewport")&&o->width>0&&o->height>0) {
      union argument a[2]={{.i=full_ui_scaled(s,o->width,0,1)},{.i=full_ui_scaled(s,o->height,1,1)}};
      wayland_send(o->p,2,a);
      wayland_send(o->surface->p,6,0);
    }
    if(o->parent==s&&!strcmp(o->p->interface->name,"wl_subsurface")) {
      union argument a[2]={{.i=full_ui_scaled(s,o->x,0,0)+ox},{.i=full_ui_scaled(s,o->y,1,0)+oy}};
      wayland_send(o->p,1,a);
    }
  }
  if(s->xdg) {
    union argument a[4]={{.i=0},{.i=0},{.i=s->full_w},{.i=s->full_h}};
    wayland_send(s->xdg->p,3,a);
  }
  wayland_send(s->p,6,0);
  /* Track the frame with the new fitted extents immediately; the canvas
   * HWC commits sparsely, so waiting for its next commit leaves the CSD
   * pieces lagging behind a resize or maximize. */
  csd_sync(s, 0, 0, s->full_w > 0 ? s->full_w : 1,
           s->full_h > 0 ? s->full_h : 1);
}
static void full_ui_request(struct object *o,const char *cls,const char *method,
                             union argument *a,struct proxy **temporary) {
  if(!o)return;
  struct object *s=full_ui_owner(!strcmp(cls,"wl_surface")?o:o->surface);
  if(!s)return;
  if(!strcmp(cls,"wp_viewport")&&!strcmp(method,"set_destination")&&a[0].i>0&&a[1].i>0) {
    a[0].i=full_ui_scaled(s,a[0].i,0,1);a[1].i=full_ui_scaled(s,a[1].i,1,1);
  }
  if(!strcmp(cls,"wl_subsurface")&&!strcmp(method,"set_position")) {
    int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
    a[0].i=full_ui_scaled(s,a[0].i,0,0)+ox;a[1].i=full_ui_scaled(s,a[1].i,1,0)+oy;
  }
  if(!strcmp(cls,"xdg_surface")&&!strcmp(method,"set_window_geometry")) {
    a[0].i=0;a[1].i=0;a[2].i=s->full_w;a[3].i=s->full_h;
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
  struct object *s=full_ui_owner(find(surface));
  if(!s)return value;
  int fw,fh,ox,oy;full_ui_box(s,&fw,&fh,&ox,&oy);
  int src=axis?s->full_canvas_h:s->full_canvas_w,dst=axis?fh:fw;
  if(src<=0||dst<=0)return value;
  int64_t n=(int64_t)(value-(axis?oy:ox))*src/dst;
  return n>INT32_MAX?INT32_MAX:n<INT32_MIN?INT32_MIN:(int32_t)n;
}
