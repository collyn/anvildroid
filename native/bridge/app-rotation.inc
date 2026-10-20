/* Stock HWC sends physical displayFrame rectangles, while ActivityManager
 * reports logical task bounds. The shell publishes both orientation and
 * dimensions from DisplayViewport in the same atomic task snapshot. */
struct app_viewport {int valid,rotation,w,h;};
static struct app_viewport app_viewport;
static int app_viewport_parse(const char *line,struct app_viewport *v) {
  int r,lw,lh,dw,dh;char extra;
  *v=(struct app_viewport){0};
  if(sscanf(line,"%d %d %d %d %d %c",&r,&lw,&lh,&dw,&dh,&extra)!=5||
     r<0||r>3||lw<160||lh<160||lw>8192||lh>8192||dw!=lw||dh!=lh)return 0;
  /* DisplayViewport.device{Width,Height} already follow its orientation.
   * HWC's boot mode retains its natural axes. */
  *v=(struct app_viewport){1,r,(r&1)?dh:dw,(r&1)?dw:dh};return 1;
}
static int app_rotation(struct object *s) {
  /* NVIDIA's exported content-frame path already supplies window-local
   * rectangles; this correction is for the stock physical-frame path. */
  return app_viewport.valid&&s&&s->app&&!s->full_ui&&!s->frame_origin_valid?
    app_viewport.rotation:0;
}
static int app_layer_rotation(struct object *layer) {
  if(!layer||layer->buffer_width<1||layer->buffer_height<1||
     layer->buffer_width>32768||layer->buffer_height>32768)return 0;
  for(struct object *sub=objects;sub;sub=sub->next)
    if(sub->surface==layer&&object_is(sub,"wl_subsurface"))return app_rotation(sub->parent);
  return 0;
}
static void app_unrotate_rect(int r,int W,int H,int *x,int *y,int *w,int *h) {
  int X=*x,Y=*y,A=*w,B=*h;
  if(r==1){*x=Y;*y=W-X-A;*w=B;*h=A;}
  if(r==2){*x=W-X-A;*y=H-Y-B;}
  if(r==3){*x=H-Y-B;*y=X;*w=B;*h=A;}
}
static void app_rotation_rect(struct object *sub,int *x,int *y,int *w,int *h) {
  int r=app_layer_rotation(sub->surface);
  app_unrotate_rect(r,task_to_logical(app_viewport.w,1),
    task_to_logical(app_viewport.h,1),x,y,w,h);
}
static void app_rotation_origin(struct object *sub,int *x,int *y) {
  struct object *vp=sub->surface?sub->surface->viewport:0;
  int w=vp?vp->width:0,h=vp?vp->height:0;
  *x=sub->x;*y=sub->y;
  if(w>0&&h>0)app_rotation_rect(sub,x,y,&w,&h);
}
static int app_rotation_transform(int transform,int r) {
  return (transform&4)|(((transform&3)+((transform&4)?r:-r)+4)&3);
}
static void app_rotation_source(struct object *layer,union argument *a) {
  int r=app_layer_rotation(layer);
  if(!r||a[2].i<1||a[3].i<1||layer->buffer_width<1||layer->buffer_height<1)return;
  int W=layer->buffer_width*256,H=layer->buffer_height*256;
  if(layer->buffer_transform&1){int tmp=W;W=H;H=tmp;}
  app_unrotate_rect(r,W,H,&a[0].i,&a[1].i,&a[2].i,&a[3].i);
}
/* Bridge/scrcpy sees logical coordinates. Only the upstream HWC listener
 * needs physical child-local coordinates before it adds displayFrame.x/y. */
static void app_rotation_input(struct proxy *surface,int32_t *x,int32_t *y) {
  struct object *layer=find(surface);
  for(struct object *sub=objects;sub;sub=sub->next) {
    if(sub->surface!=layer||!object_is(sub,"wl_subsurface"))continue;
    int r=app_layer_rotation(layer);struct object *vp=layer->viewport;
    if(!r||!vp)return;
    int32_t X=*x,Y=*y;int64_t W=256LL*vp->width,H=256LL*vp->height;
    int64_t a=X,b=Y;
    if(r==1){a=W-Y;b=X;}if(r==2){a=W-X;b=H-Y;}if(r==3){a=Y;b=H-X;}
    *x=a>INT32_MAX?INT32_MAX:a<INT32_MIN?INT32_MIN:(int32_t)a;
    *y=b>INT32_MAX?INT32_MAX:b<INT32_MIN?INT32_MIN:(int32_t)b;return;
  }
}
