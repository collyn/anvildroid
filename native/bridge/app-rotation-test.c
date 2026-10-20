/* Reproduce the real rotation failure with the stock HWC protocol ordering:
 * physical child frames and transformed crops under a logical portrait task. */
#include "bridge.c"
#include <assert.h>
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static const struct interface si={.name="wl_surface"},vi={.name="wp_viewport"},
  subi={.name="wl_subsurface"},xi={.name="xdg_surface"};
static struct proxy parent={&si},child={&si},pv={&vi},cv={&vi},sub={&subi},xdg={&xi};
static int position[2],destination[2],source[4],transform;
static uint32_t version(struct proxy *p){(void)p;return 4;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)i;(void)v;
  if(p==&sub&&op==1){position[0]=a[0].i;position[1]=a[1].i;}
  if(p==&cv&&op==2){destination[0]=a[0].i;destination[1]=a[1].i;}
  if(p==&cv&&op==1)for(int j=0;j<4;++j)source[j]=a[j].i;
  if(p==&child&&op==7)transform=a[0].i;
  return 0;
}
/* Independent forward rectangle oracle: logical Android to physical HWC. */
static void physical(int q,int W,int H,int *x,int *y,int *w,int *h) {
  int X=*x,Y=*y,A=*w,B=*h;
  if(q==1){*x=W-Y-B;*y=X;*w=B;*h=A;}
  if(q==2){*x=W-X-A;*y=H-Y-B;}
  if(q==3){*x=Y;*y=H-X-A;*w=B;*h=A;}
}
int main(void) {
  struct game_fit_state fit={0};
  struct object root={.p=&parent,.app=1,.app_id="org.anvildroid.touchprobe",
    .region_valid=1,.fit=&fit,.fit_w=448,.fit_h=704,.fit_delivered_w=448,.fit_delivered_h=704};
  struct object xs={.p=&xdg,.surface=&root};root.xdg=&xs;
  struct object rootvp={.p=&pv,.surface=&root,.width=3264,.height=2064};root.viewport=&rootvp;
  struct object layer={.p=&child,.buffer_width=472,.buffer_height=728};
  struct object vp={.p=&cv,.surface=&layer};layer.viewport=&vp;
  struct object ss={.p=&sub,.parent=&root,.surface=&layer};
  root.next=&rootvp;rootvp.next=&layer;layer.next=&vp;vp.next=&ss;ss.next=&xs;objects=&root;
  for(struct object *o=objects;o;o=o->next)o->protocol_name=o->p->interface->name;
  real_marshal=record;get_version=version;scale_has_viewporter=1;hwc_scale_120=120;
  task_count=1;tasks[0]=(struct task){.id=26,.package="org.anvildroid.touchprobe",.x=0,.y=32,.width=448,.height=704};
  for(int q=0;q<4;++q) {
    char line[128];int dw=(q&1)?2064:3264,dh=(q&1)?3264:2064;
    snprintf(line,sizeof(line),"%d %d %d %d %d",q,dw,dh,dw,dh);
    assert(app_viewport_parse(line,&app_viewport));
    assert(app_viewport.w==3264&&app_viewport.h==2064);
    ss.x=-12;ss.y=20;vp.width=472;vp.height=728;
    physical(q,3264,2064,&ss.x,&ss.y,&vp.width,&vp.height);
    layer.buffer_transform=q;vp.source_x=vp.source_y=0;
    vp.source_width=vp.width*256;vp.source_height=vp.height*256;
    transform=q;geometry(&root);
    assert(position[0]==-12&&position[1]==-12);
    assert(destination[0]==472&&destination[1]==728&&transform==0);
    if(q)assert(source[0]==0&&source[1]==0&&source[2]==472*256&&source[3]==728*256);
    /* Arbitrary off-centre crops rotate about the transformed BUFFER, not
     * about their own origin. Merely swapping axes fails this assertion. */
    int x=11,y=17,w=400,h=650;
    physical(q,(q&1)?728:472,(q&1)?472:728,&x,&y,&w,&h);
    union argument crop[4]={{.i=x*256},{.i=y*256},{.i=w*256},{.i=h*256}};
    app_rotation_source(&layer,crop);
    assert(crop[0].i==11*256&&crop[1].i==17*256&&crop[2].i==400*256&&crop[3].i==650*256);
    /* Input has independent logical and upstream-HWC representations. */
    int ox,oy;app_rotation_origin(&ss,&ox,&oy);assert(ox==-12&&oy==20);
    int32_t ix=100*256,iy=200*256;app_rotation_input(&child,&ix,&iy);
    int ex=100,ey=200,ew=0,eh=0;
    physical(q,vp.width,vp.height,&ex,&ey,&ew,&eh);
    assert(ix==ex*256&&iy==ey*256);
    /* Real request rewriting also handles HWC's child-commit-before-position. */
    struct proxy *temporary=0;
    union argument pos[2]={{.i=ss.x},{.i=ss.y}};
    game_fit_request(&ss,"wl_subsurface","set_position",pos,&temporary);
    assert(pos[0].i==-12&&pos[1].i==-12);
    union argument dim[2]={{.i=vp.width},{.i=vp.height}};
    game_fit_request(&vp,"wp_viewport","set_destination",dim,&temporary);
    assert(dim[0].i==472&&dim[1].i==728);
    if(q){union argument damage[4]={{.i=-1},{.i=-1},{.i=INT32_MAX},{.i=INT32_MAX}};
      game_fit_request(&layer,"wl_surface","damage",damage,&temporary);
      assert(damage[0].i==0&&damage[1].i==0&&damage[2].i==472&&damage[3].i==728);}
  }
  const int transform_oracle[8][4]={{0,3,2,1},{1,0,3,2},{2,1,0,3},{3,2,1,0},
    {4,5,6,7},{5,6,7,4},{6,7,4,5},{7,4,5,6}};
  for(int t=0;t<8;++t)for(int q=0;q<4;++q)
    assert(app_rotation_transform(t,q)==transform_oracle[t][q]);
  assert(!app_viewport_parse("1 2064 3264 3264 2064",&app_viewport));
  assert(!app_viewport.valid&&!app_rotation(&root));
  assert(!app_viewport_parse("4 2064 3264 2064 3264",&app_viewport));
  assert(!app_viewport_parse("1 2064 3264 2064 3264 trailing",&app_viewport));
  assert(app_viewport_parse("1 2064 3264 2064 3264",&app_viewport));
  layer.buffer_width=0;assert(!app_layer_rotation(&layer));
  union argument unknown[4]={{.i=10},{.i=20},{.i=30},{.i=40}};
  app_rotation_source(&layer,unknown);assert(unknown[0].i==10&&unknown[1].i==20);
  root.full_ui=1;assert(!app_rotation(&root));root.full_ui=0;
  root.frame_origin_valid=1;assert(!app_rotation(&root));
  return 0;
}
