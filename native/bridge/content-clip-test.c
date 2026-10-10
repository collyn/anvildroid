#include "bridge.c"
#include <assert.h>

int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static const struct message surface_methods[] = {
  {"destroy","",0},{"attach","?oii",0},{"damage","iiii",0},
  {"frame","n",0},{"set_opaque_region","?o",0},
  {"set_input_region","?o",0},{"commit","",0}
};
static const struct message viewport_methods[] = {
  {"destroy","",0},{"set_source","ffff",0},{"set_destination","ii",0}
};
static const struct message sub_methods[] = {{"destroy","",0},{"set_position","ii",0}};
static const struct interface si={.name="wl_surface",.methods=surface_methods},
  vi={.name="wp_viewport",.methods=viewport_methods},
  subi={.name="wl_subsurface",.methods=sub_methods},
  xi={.name="xdg_surface"},bi={.name="wl_buffer"};
static struct proxy root_p={&si},layer_p={&si},vp_p={&vi},sub_p={&subi},xdg_p={&xi},buffer_p={&bi};
static int px,py,dw,dh,source[4],commits,detaches,attaches;
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                           const struct interface *i,uint32_t v) {
  (void)i;(void)v;
  if(p==&sub_p&&op==1){px=a[0].i;py=a[1].i;}
  if(p==&vp_p&&op==1)for(int j=0;j<4;j++)source[j]=a[j].i;
  if(p==&vp_p&&op==2){dw=a[0].i;dh=a[1].i;}
  if(p==&layer_p&&op==6)commits++;
  if(p==&layer_p&&op==1){if(a[0].o)attaches++;else detaches++;}
  return 0;
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static void send_request(struct proxy *p,int op,union argument *a) {
  wl_proxy_marshal_array_constructor_versioned(p,(uint32_t)op,a,0,1);
}
int main(void) {
  real_marshal=record;get_version=version;
  struct object *root=track(&root_p),*layer=track(&layer_p),*vp=track(&vp_p);
  struct object *sub=track(&sub_p),*xdg=track(&xdg_p);
  track(&buffer_p);
  struct object region={.valid=1,.x=80,.y=32,.width=800,.height=600};
  root->app=1;root->region=&region;root->xdg=xdg;xdg->surface=root;
  layer->viewport=vp;vp->surface=layer;sub->surface=layer;sub->parent=root;
  union argument crop[4]={{.i=4*256},{.i=8*256},{.i=1040*256},{.i=790*256}};
  union argument dest[2]={{.i=832},{.i=632}},pos[2]={{.i=64},{.i=16}};
  union argument attach[3]={{.o=&buffer_p},{.i=0},{.i=0}};
  send_request(&vp_p,1,crop);send_request(&vp_p,2,dest);
  send_request(&sub_p,1,pos);send_request(&layer_p,1,attach);
  geometry(root);
  assert(px==80&&py==32&&dw==800&&dh==600);
  assert(source[0]==24*256&&source[1]==28*256);
  assert(source[2]==1000*256&&source[3]==750*256);
  assert(content_clip_input(&layer_p,10*256,0)==26*256);
  assert(content_clip_input(&layer_p,20*256,1)==36*256);
  assert(content_clip_input(&root_p,10*256,0)==10*256);
  assert(vp->source_width==1040*256&&vp->width==832&&sub->x==64);

  /* Exercise the real configure callback while Android is deliberately frozen. */
  struct object top={.surface=root};
  uint32_t resizing=3;
  struct {size_t size,alloc;uint32_t *data;} states={4,4,&resizing};
  const int sizes[][2]={{465,400},{300,250},{900,700},{465,400}};
  for(unsigned j=0;j<sizeof(sizes)/sizeof(sizes[0]);j++) {
    configure(&top,0,sizes[j][0],sizes[j][1],&states);
    geometry(root);
    assert(px>=root->bx&&py>=root->by);
    assert(px+dw<=root->bx+root->bw&&py+dh<=root->by+root->bh);
    assert(!resize_due(&root->resize));
    /* A fresh HWC crop/destination must not bypass clipping on child commit. */
    send_request(&vp_p,1,crop);send_request(&vp_p,2,dest);
    send_request(&sub_p,1,pos);send_request(&layer_p,6,0);
    assert(px+dw<=root->bx+root->bw&&py+dh<=root->by+root->bh);
    assert(source[0]+source[2]<=crop[0].i+crop[2].i);
    assert(source[1]+source[3]<=crop[1].i+crop[3].i);
  }
  assert(dw==465&&dh==400&&commits>0);
  resize_release(&root->resize);
  geometry(root);assert(dw==465&&dh==400); /* Still waiting for Android. */
  root->preview_width=root->preview_height=0;
  geometry(root);assert(dw==800&&dh==600); /* Cancel restores the original crop. */

  /* Off-window popup/layer: hide it, then restore it without a new frame. */
  pos[0].i=1000;send_request(&sub_p,1,pos);send_request(&layer_p,6,0);
  assert(layer->clip_hidden&&detaches==1);
  geometry(root);assert(detaches==1);
  pos[0].i=64;send_request(&sub_p,1,pos);geometry(root);
  assert(!layer->clip_hidden&&attaches==2&&dw==800);
  pos[0].i=1000;send_request(&sub_p,1,pos);geometry(root);
  forget_proxy(&buffer_p);
  pos[0].i=64;send_request(&sub_p,1,pos);geometry(root);
  assert(layer->clip_hidden&&attaches==2); /* Never reattach a destroyed proxy. */
  track(&buffer_p);send_request(&layer_p,1,attach);send_request(&layer_p,6,0);
  assert(!layer->clip_hidden&&attaches==3&&dw==800);
  /* A one-pixel sliver of a stretched solid-color layer still clips. */
  crop[0].i=crop[1].i=0;crop[2].i=crop[3].i=256;
  pos[0].i=879;pos[1].i=631;
  send_request(&vp_p,1,crop);send_request(&sub_p,1,pos);send_request(&layer_p,6,0);
  assert(px==879&&py==631&&dw==1&&dh==1);
  assert(source[2]==1&&source[3]==1);
  while(objects)forget_proxy(objects->p);
  return 0;
}
