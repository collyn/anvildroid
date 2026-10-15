static char display_test_dir[]="/tmp/anvil-full-display-XXXXXX";
#define ANVIL_DISPLAY_DIRECTORY display_test_dir
#include "bridge.c"
#include <assert.h>
extern char *mkdtemp(char *);
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static const struct interface si={.name="wl_surface"},vi={.name="wp_viewport"},subi={.name="wl_subsurface"};
static struct proxy root={&si},child={&si},viewport={&vi},subsurface={&subi};
static int source[4],dest[2],pos[2];
static uint32_t version(struct proxy *p){(void)p;return 1;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,const struct interface *i,uint32_t v) {
  (void)i;(void)v;
  if(p==&viewport&&op==1)for(int n=0;n<4;++n)source[n]=a[n].i;
  if(p==&viewport&&op==2)for(int n=0;n<2;++n)dest[n]=a[n].i;
  if(p==&subsurface&&op==1)for(int n=0;n<2;++n)pos[n]=a[n].i;
  return 0;
}
int main(void) {
  assert(mkdtemp(display_test_dir));real_marshal=record;get_version=version;
  struct object s={.p=&root,.full_ui=1,.full_w=720,.full_h=1280,.full_canvas_w=1280,.full_canvas_h=720};
  struct object layer={.p=&child},vp={.p=&viewport,.surface=&layer,.width=1280,.height=720,.source_width=1280*256,.source_height=720*256};
  struct object sub={.p=&subsurface,.surface=&layer,.parent=&s};
  layer.viewport=&vp;s.next=&layer;layer.next=&vp;vp.next=&sub;objects=&s;
  assert(display_size_request(720,1280,1));
  char path[512];snprintf(path,sizeof(path),"%s/display-state",display_test_dir);
  void *f=fopen(path,"w");assert(f);fprintf(f,"1280 720 1280 720 0 0 1280 720\n");fclose(f);
  assert(!display_size_refresh()&&display_pending_w==720); /* stale ack */
  f=fopen(path,"w");assert(f);fprintf(f,"720 1280 1280 720 437 0 405 720\n");fclose(f);
  assert(display_size_refresh()&&!display_pending_w);
  union argument root_size[2]={{.i=1280},{.i=720}};struct proxy *temporary=0;
  full_ui_request(&(struct object){.surface=&s},"wp_viewport","set_destination",root_size,&temporary);
  assert(root_size[0].i==720&&root_size[1].i==1280);
  full_ui_clip(&layer);
  assert(source[0]==437*256&&source[1]==0&&source[2]==405*256&&source[3]==720*256);
  assert(dest[0]==720&&dest[1]==1280&&pos[0]==0&&pos[1]==0);
  assert(full_ui_input(&root,0,0)==437*256&&full_ui_input(&child,0,0)==437*256);
  assert(full_ui_input(&child,720*256,0)==842*256&&full_ui_input(&child,1280*256,1)==720*256);
  /* A partial physical layer keeps its own input origin after cropping. */
  sub.x=400;vp.width=100;vp.source_width=100*256;
  full_ui_clip(&layer);
  assert(source[0]==37*256&&source[2]==63*256&&pos[0]==0);
  assert(full_ui_input(&child,0,0)==37*256);
  struct display_crop c;
  assert(display_crop_layer(0,0,100,100,0,0,256,256,&c)==-1);
  assert(!display_size_request(0,720,1)&&!display_size_request(9000,720,0));
  /* The acknowledged logical dimensions control orientation automatically. */
  assert(display_size_request(1280,720,1));
  f=fopen(path,"w");assert(f);fprintf(f,"1280 720 1280 720 0 0 1280 720\n");fclose(f);
  assert(display_size_refresh());s.full_w=1280;s.full_h=720;
  sub.x=0;vp.width=1280;vp.source_width=1280*256;full_ui_clip(&layer);
  assert(dest[0]==1280&&dest[1]==720&&source[0]==0&&source[2]==1280*256);
  /* Sharp path: physical projection equals logical dimensions, so the
   * Android buffer is cropped at 1:1 rather than reduced then enlarged. */
  assert(display_size_request(720,1280,1));
  f=fopen(path,"w");assert(f);fprintf(f,"720 1280 1280 1280 280 0 720 1280\n");fclose(f);
  assert(display_size_refresh());s.full_w=720;s.full_h=1280;s.full_canvas_h=1280;
  vp.height=1280;vp.source_height=1280*256;full_ui_clip(&layer);
  assert(source[0]==280*256&&source[2]==720*256&&source[3]==1280*256);
  assert(dest[0]*256==source[2]&&dest[1]*256==source[3]);
  /* Default HWC ordering: child commit, then set_position, parent commit.
   * The crop's left inset must survive the later position request. */
  display_projection=(struct display_projection){720,1280,1984,1280,632,0,720,1280};
  s.full_canvas_w=1984;vp.width=1984;vp.source_width=1984*256;
  sub.x=0;sub.y=0;
  full_ui_request(&layer,"wl_surface","commit",0,&temporary);
  assert(source[0]==632*256&&source[2]==720*256&&pos[0]==0);
  union argument position[2]={{.i=0},{.i=0}};
  full_ui_request(&sub,"wl_subsurface","set_position",position,&temporary);
  assert(position[0].i==0&&position[1].i==0);
  /* Even a new final HWC position is reconciled before parent commit. */
  sub.x=632;vp.width=720;vp.source_width=720*256;
  full_ui_request(&s,"wl_surface","commit",0,&temporary);
  assert(source[0]==0&&source[2]==720*256&&pos[0]==0);
  assert(layer.full_clip_x==0&&full_ui_input(&child,0,0)==0);
  /* Custom accepts the compositor's aspect ratio, not the old preset's. */
  s.custom_size.enabled=1;
  struct object top={.surface=&s};
  struct {size_t size,alloc;uint32_t *data;} states={0,0,0};
  configure(&top,0,900,700,&states);
  assert(s.full_w==900&&s.full_h==700&&s.custom_size.w==900&&s.custom_size.h==700);
  s.custom_size.enabled=0;s.full_w=720;s.full_h=1280;
  configure(&top,0,900,700,&states);
  assert(s.full_w<900&&s.full_h==700); /* Preset retains portrait aspect. */
  return 0;
}
