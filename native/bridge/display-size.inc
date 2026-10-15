/* Shell companion acknowledges Android's logical display and its physical
 * projection. Never mistake host geometry for an Android display resize. */
#ifndef ANVIL_NATIVE_STDIO
extern void *fopen(const char *,const char *);
extern int fclose(void *);
extern int fprintf(void *,const char *,...);
extern int fscanf(void *,const char *,...);
extern int rename(const char *,const char *);
#endif
extern int snprintf(char *,size_t,const char *,...);
#ifndef ANVIL_DISPLAY_DIRECTORY
#define ANVIL_DISPLAY_DIRECTORY "/data/waydroid_tmp/anvildroid"
#endif
struct display_projection {int lw,lh,pw,ph,x,y,w,h;};
static struct display_projection display_projection;
static int display_pending_w,display_pending_h;
static int display_size_request(int w,int h,int full) {
  if(w<160||h<160||w>8192||h>8192)return 0;
  char temporary[512],request[512];
  snprintf(temporary,sizeof(temporary),"%s/display-size.tmp",ANVIL_DISPLAY_DIRECTORY);
  snprintf(request,sizeof(request),"%s/display-size",ANVIL_DISPLAY_DIRECTORY);
  void *f=fopen(temporary,"w");
  if(!f)return 0;
  int ok=fprintf(f,"%d %d %s\n",w,h,full?"full":"canvas")>0;
  if(fclose(f))ok=0;
  if(!ok||rename(temporary,request))return 0;
  display_pending_w=w;display_pending_h=h;return 1;
}
static int display_size_refresh(void) {
  struct display_projection n;char extra;
  char path[512];snprintf(path,sizeof(path),"%s/display-state",ANVIL_DISPLAY_DIRECTORY);
  void *f=fopen(path,"r");
  if(!f)return 0;
  int count=fscanf(f,"%d %d %d %d %d %d %d %d %c",&n.lw,&n.lh,&n.pw,&n.ph,&n.x,&n.y,&n.w,&n.h,&extra);
  fclose(f);
  if(count!=8||n.lw<160||n.lh<160||n.lw>8192||n.lh>8192||
     n.pw<1||n.ph<1||n.pw>32768||n.ph>32768||n.x<0||n.y<0||
     n.w<1||n.h<1||n.w>n.pw||n.h>n.ph||n.x>n.pw-n.w||n.y>n.ph-n.h)return 0;
  if(display_pending_w&&(n.lw!=display_pending_w||n.lh!=display_pending_h))return 0;
  display_pending_w=display_pending_h=0;
  int changed=n.lw!=display_projection.lw||n.lh!=display_projection.lh||
    n.pw!=display_projection.pw||n.ph!=display_projection.ph||
    n.x!=display_projection.x||n.y!=display_projection.y||n.w!=display_projection.w||n.h!=display_projection.h;
  display_projection=n;return changed;
}
/* Crop a physical HWC layer to the acknowledged Android display projection.
 * Source dimensions use Wayland 24.8 fixed point; round outwards. */
struct display_crop {int left,top,w,h,sx,sy,sw,sh;};
static int display_crop_layer(int x,int y,int w,int h,int sx,int sy,int sw,int sh,
                              struct display_crop *c) {
  struct display_projection *p=&display_projection;
  if(!p->w||w<1||h<1||sw<1||sh<1)return 0;
  int l=p->x>x?p->x-x:0,t=p->y>y?p->y-y:0;
  int r=p->x+p->w-x,b=p->y+p->h-y;
  if(r>w)r=w;
  if(b>h)b=h;
  if(l>=r||t>=b)return -1;
  int u=(int)((int64_t)l*sw/w),v=(int)((int64_t)t*sh/h);
  c->left=l;c->top=t;c->w=r-l;c->h=b-t;
  c->sx=sx+u;c->sy=sy+v;
  c->sw=(int)(((int64_t)r*sw+w-1)/w)-u;
  c->sh=(int)(((int64_t)b*sh+h-1)/h)-v;
  return 1;
}
