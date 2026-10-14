#define ANVIL_KEYMAP_EDITOR 1
#define ANVIL_GAME_FIT 1
#include "bridge.c"
#include <assert.h>
int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static struct proxy *pointer_proxy,*cursor_proxy;
static int shows,enters,leaves,destroys;
static uint32_t expected_serial;
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)i;(void)v;
  if(p==pointer_proxy&&op==0) {
    assert(a[0].u==expected_serial&&a[1].o==cursor_proxy);
    assert(a[2].i==3&&a[3].i==2);++shows;
  }
  return NULL;
}
static void destroy(struct proxy *p){assert(p==cursor_proxy);++destroys;}
static void enter(void *data,struct proxy *p,uint32_t serial,struct proxy *s,int32_t x,int32_t y) {
  (void)p;(void)serial;(void)s;(void)x;(void)y;
  ++enters;
  /* Android hides its cursor for this unknown overlay surface. */
  union argument a[4]={{.u=999},{.o=0},{.i=0},{.i=0}};
  csd_cursor_request(data,a);
  if(csd_cursor_owned(data))assert(a[1].o==cursor_proxy&&a[0].u==expected_serial);
  else assert(!a[1].o&&a[0].u==999);
}
static void leave(void *data,struct proxy *p,uint32_t serial,struct proxy *s) {
  (void)data;(void)p;(void)serial;(void)s;++leaves;
}
int main(void) {
  real_marshal=record;get_version=version;real_destroy=destroy;
  struct proxy pointer={0},cursor={0},editor_surface={0},content={0},menu_surface={0};
  pointer_proxy=&pointer;cursor_proxy=&cursor;
  void (*listener[])(void)={(void (*)(void))enter,(void (*)(void))leave};
  struct keymap_overlay editor={0};editor.piece.surface=&editor_surface;
  struct csd_frame frame={.menu_open=1,.menu_hover=-1};
  frame.menu.surface=&menu_surface;frame.menu.width=800;frame.menu.height=600;
  struct object app={.p=&content,.overlay=&editor,.csd=&frame};objects=&app;
  struct object top={.surface=&app};frame.top=&top;
  struct object o={.p=&pointer,.edit_cursor=&cursor,.listener=listener};o.listener_data=&o;
  expected_serial=42;
  csd_pointer_enter(&o,&pointer,42,&editor_surface,10*256,10*256);
  assert(enters==1&&shows==1&&o.overlay_focus==&app);
  csd_pointer_leave(&o,&pointer,43,&editor_surface);
  assert(leaves==1&&!o.pointer_surface&&!csd_cursor_owned(&o));
  csd_cursor_show(&o);assert(shows==1);
  /* Serial zero is valid after wraparound; it is not an absence marker. */
  expected_serial=0;
  csd_pointer_enter(&o,&pointer,0,&editor_surface,0,0);assert(shows==2);
  csd_pointer_leave(&o,&pointer,1,&editor_surface);
  csd_pointer_enter(&o,&pointer,2,&content,0,0);
  assert(shows==2&&!csd_cursor_owned(&o));
  expected_serial=3;
  csd_pointer_enter(&o,&pointer,3,&menu_surface,500*256,100*256);
  assert(shows==3&&o.menu_focus==&frame);
  frame.menu_open=0;
  union argument a[4]={{.u=4},{.o=0},{.i=9},{.i=8}};
  csd_cursor_request(&o,a);assert(a[0].u==4&&!a[1].o&&a[2].i==9);
  csd_cursor_destroy(&o);csd_cursor_destroy(&o);assert(destroys==1&&!o.edit_cursor);
  int ink=0,smooth=0;
  for(int y=0;y<32;++y)for(int x=0;x<26;++x) {
    uint32_t pixel=csd_cursor_pixel(x,y),alpha=pixel>>24;
    assert((pixel&255)<=alpha&&((pixel>>8)&255)<=alpha&&((pixel>>16)&255)<=alpha);
    ink+=alpha>0;smooth+=alpha>0&&alpha<255;
  }
  assert(ink>100&&smooth>0&&!csd_cursor_pixel(0,0)&&!csd_cursor_pixel(25,31));
  return 0;
}
