#include "bridge.c"
#include <assert.h>
int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static const struct interface pool_i={.name="wl_shm_pool"},buffer_i={.name="wl_buffer"},
  region_i={.name="wl_region"};
static struct proxy pool,buffer,region,shm,parent,xdg_proxy,chrome[4],sub[4];
static int window_geometry[4];
static int px[4],py[4],attached[4],empty_input[4],normal_input[4],transparent_buffers,black_strip;
static uint32_t version(struct proxy *p){(void)p;return 1;}
static void destroy(struct proxy *p){(void)p;}
static int listen_buffer(struct proxy *p,void (**f)(void),void *data) {
  (void)p;(void)f;(void)data;return 0;
}
static int last_region_nonempty;
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)v;
  if(p==&xdg_proxy&&op==3)for(int n=0;n<4;++n)window_geometry[n]=a[n].i;
  if(i==&pool_i) {
    int size=a[2].i;uint32_t *pixels=calloc(1,size);assert(pixels);
    assert(read(a[1].i,pixels,size)==size);
    int nonzero=0;for(int n=0;n<size/4;++n)nonzero+=pixels[n]!=0;
    if(!nonzero)++transparent_buffers;
    /* Overflow paint retains transparent exterior margins. */
    if(size==(800+2*CSD_RESIZE)*(50+CSD_RESIZE)*4) {
      for(int y=0;y<50+CSD_RESIZE;++y)for(int x=0;x<800+2*CSD_RESIZE;++x)
        assert(pixels[y*(800+2*CSD_RESIZE)+x]==
          (y<50&&x>=CSD_RESIZE&&x<800+CSD_RESIZE?0xff000000u:0));
      ++black_strip;
    }
    free(pixels);return &pool;
  }
  if(i==&buffer_i)return &buffer;
  if(i==&region_i){last_region_nonempty=0;return &region;}
  if(p==&region&&op==1)last_region_nonempty=1;
  for(int n=0;n<4;++n) {
    if(p==&sub[n]&&op==1){px[n]=a[0].i;py[n]=a[1].i;}
    if(p==&chrome[n]&&op==1)attached[n]=a[0].o!=0;
    if(p==&chrome[n]&&op==5) {
      if(last_region_nonempty)++normal_input[n];else ++empty_input[n];
    }
  }
  return 0;
}
int main(void) {
  assert(CSD_BORDER==1&&CSD_RESIZE==5&&CSD_CORNER==24);
  /* Maximize fills only the thin geometry band; outside input stays clear. */
  for(int y=0;y<338;++y)for(int x=0;x<5;++x) {
    assert(csd_edge_pixel(1,x,y,5,338,1)==(x==4&&y<334?0xff212121u:0));
    assert(csd_edge_pixel(2,x,y,5,338,1)==(x==0&&y<334?0xff212121u:0));
    assert(!csd_edge_pixel(1,x,y,5,338,0));
  }
  for(int y=0;y<5;++y)for(int x=0;x<810;++x)
    assert(csd_edge_pixel(3,x,y,810,5,1)==(y==0&&x>=4&&x<806?0xff212121u:0));
  assert(csd_header_pixel(0,10,802,33,1)==0xff212121);
  assert(!csd_header_pixel(0,10,802,33,0));
  real_marshal=record;get_version=version;real_destroy=destroy;real_listen=listen_buffer;
  csd_shm=csd_compositor=&shm;csd_pool_i=&pool_i;csd_buffer_i=&buffer_i;csd_region_i=&region_i;
  struct object s={.p=&parent,.app=1};struct object top={.surface=&s};
  struct csd_frame f={.top=&top};s.csd=&f;objects=&s;
  for(int n=0;n<4;++n)f.pieces[n]=(struct csd_piece){.surface=&chrome[n],.subsurface=&sub[n]};
  csd_sync(&s,0,0,800,300);
  assert(f.pieces[0].width==802&&f.pieces[0].height==33);
  assert(px[0]==-1&&py[0]==-33);
  assert(f.pieces[1].width==5&&f.pieces[2].width==5&&f.pieces[3].height==5);
  assert(px[1]==-5&&px[2]==800&&py[1]==-33&&py[2]==-33);
  assert(px[3]==-5&&py[3]==300&&f.pieces[3].width==810);
  assert(transparent_buffers==3);
  for(int n=0;n<4;++n)assert(attached[n]&&normal_input[n]==1);
  for(int x=0;x<5;++x) {
    assert(csd_hit(1,x,100,5,338)==4&&csd_hit(2,x,100,5,338)==8);
    assert(csd_hit(1,x,23,5,338)==5&&csd_hit(2,x,23,5,338)==9);
    assert(csd_hit(1,x,314,5,338)==6&&csd_hit(2,x,314,5,338)==10);
  }
  assert(csd_hit(3,23,4,810,5)==6&&csd_hit(3,24,4,810,5)==2);
  assert(csd_hit(3,786,4,810,5)==10&&csd_hit(3,785,4,810,5)==2);
  assert(csd_hit(0,23,4,802,33)==5&&csd_hit(0,24,4,802,33)==1);
  /* Maximize preserves the same header appearance and input-only margins. */
  f.maximized=1;csd_sync(&s,0,0,1000,600);
  assert(f.pieces[0].width==1002&&f.pieces[0].height==33&&f.pieces[1].width==5);
  f.fullscreen=1;f.menu_open=1;csd_sync(&s,0,0,1000,600);
  for(int n=0;n<4;++n) {
    assert(!attached[n]&&!f.pieces[n].width&&!f.pieces[n].height&&empty_input[n]==1);
    assert(!csd_frame_hit(&f,n,0,0,5,600));
  }
  assert(!f.menu_open);
  f.fullscreen=f.maximized=0;csd_sync(&s,0,0,800,300);
  for(int n=0;n<4;++n)assert(attached[n]&&normal_input[n]==3);
  assert(f.pieces[0].width==802&&transparent_buffers==6);
  s.full_ui=1;s.full_canvas_h=250;csd_sync(&s,0,0,800,300);
  assert(black_strip==1&&py[3]==250);
  /* The Waydroid canvas uses the same frame but its own configure path.
   * Verify fitted content, thin xdg bounds and removal/restoration of grips. */
  struct object xdg={.p=&xdg_proxy};s.xdg=&xdg;s.app=0;
  s.full_canvas_w=1600;s.full_canvas_h=900;s.full_w=s.full_h=0;
  uint32_t state=0;
  struct {size_t size,alloc;uint32_t *data;} states={.data=&state};
  configure(&top,0,800+2*CSD_BORDER,450+CSD_HEADER+CSD_BORDER,&states);
  full_ui_apply(&s);
  assert(s.full_w==800&&s.full_h==450&&f.pieces[1].width==5&&f.pieces[3].height==5);
  assert(window_geometry[0]==-1&&window_geometry[1]==-33&&
         window_geometry[2]==802&&window_geometry[3]==484);
  state=1;states.size=sizeof(state);
  configure(&top,0,1200+2*CSD_BORDER,800+CSD_HEADER+CSD_BORDER,&states);
  full_ui_apply(&s);
  assert(f.maximized&&!f.fullscreen&&f.pieces[2].width==5);
  assert(window_geometry[2]==1202&&window_geometry[3]==834);
  state=2;configure(&top,0,1200,800,&states);full_ui_apply(&s);
  assert(f.fullscreen&&window_geometry[0]==0&&window_geometry[1]==0&&
         window_geometry[2]==1200&&window_geometry[3]==800);
  for(int n=0;n<4;++n)assert(!attached[n]&&!f.pieces[n].width);
  states.size=0;configure(&top,0,802,484,&states);full_ui_apply(&s);
  assert(!f.fullscreen&&!f.maximized&&s.full_w==800&&s.full_h==450);
  for(int n=0;n<4;++n)assert(attached[n]);
  assert(f.pieces[1].width==5&&f.pieces[3].height==5&&window_geometry[2]==802);
  /* Repaint even if a maximize/restore changes only state, not dimensions. */
  s.full_ui=0;csd_sync(&s,0,0,800,300);
  int before=normal_input[1];
  f.maximized=1;csd_sync(&s,0,0,800,300);assert(normal_input[1]==before+1);
  f.maximized=0;csd_sync(&s,0,0,800,300);assert(normal_input[1]==before+2);
  return 0;
}
