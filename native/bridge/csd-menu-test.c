#define ANVIL_KEYMAP_EDITOR 1
#include "bridge.c"
#include <assert.h>
extern int setenv(const char *,const char *,int);
int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static uint32_t version(struct proxy *p){(void)p;return 1;}
static int commits;
static struct proxy stack_surface[6],stack_sub[6];
static int stack_order[6],stack_enabled;
static int stack_position(int id) {
  for(int i=0;i<6;++i)if(stack_order[i]==id)return i;
  assert(0);return -1;
}
static void stack_request(struct proxy *p,uint32_t op,union argument *a) {
  if(!stack_enabled||(op!=2&&op!=3)||!a)return;
  int id=-1,sibling=-1;
  for(int i=0;i<6;++i){if(p==&stack_sub[i])id=i;if(a[0].o==&stack_surface[i])sibling=i;}
  if(id<0||sibling<0)return;
  int old=stack_position(id);
  for(int i=old;i<5;++i)stack_order[i]=stack_order[i+1];
  int at=0;while(at<5&&stack_order[at]!=sibling)++at;
  if(op==2)++at;
  for(int i=5;i>at;--i)stack_order[i]=stack_order[i-1];
  stack_order[at]=id;
}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *i,uint32_t v) {
  (void)p;(void)a;(void)i;(void)v;
  stack_request(p,op,a);
  if(op==6)++commits;
  return NULL;
}
int main(void) {
  real_marshal=record;get_version=version;
  struct proxy root={0},top_proxy={0};
  struct object s={.p=&root,.app=1,.has_bounds=1,.app_id="com.test",
    .content_width=800,.content_height=600};
  struct object top={.p=&top_proxy,.surface=&s};
  struct csd_frame f={.top=&top,.menu_hover=-1};s.csd=top.csd=&f;
  objects=&s;task_count=1;tasks[0]=(struct task){.id=7,.package="com.test"};
  assert(csd_frame_hit(&f,0,18,18,800,33)==CSD_MENU);
  assert(csd_frame_hit(&f,0,18,2,800,33)==5); /* top resize grip */
  assert(csd_frame_hit(&f,0,800-18,18,800,33)==CSD_CLOSE);
  s.app=0;assert(csd_frame_hit(&f,0,18,18,800,33)==CSD_MOVE);s.app=1;
  assert(csd_menu_row_at(10,5,800,600,0)==0);
  assert(csd_menu_row_at(500,5,800,600,0)==-1);
  assert(csd_menu_row_at(10,36,160,64,0)==1);
  assert(csd_menu_row_at(10,68,300,300,0)==CSD_MENU_SIZE);
  assert(csd_menu_row_at(10,100,300,300,0)==-1);
  assert(csd_menu_row_at(210,40,800,600,1)==2);
  assert(csd_menu_row_at(210,72,800,600,1)==3);
  assert(csd_menu_row_at(210,40,800,600,0)==-1);
  assert(csd_menu_row_at(10,5,160,300,1)==1); /* back */
  assert(csd_menu_row_at(10,36,160,300,1)==2);
  assert(csd_menu_row_at(10,68,160,300,1)==3);
  uint32_t pixels[300*300]={0};
  csd_menu_paint_context=&f;
  csd_menu_paint(pixels,300,300);
  assert(pixels[299*300+299]==0); /* transparent outside dismissal cover */
  assert(pixels[3*300+3]==0xff282828);
  /* Text uses the full buffer stride, not the narrower panel width. */
  int ink=0,antialias=0;
  for(int y=8;y<30;++y)for(int x=12;x<90;++x) {
    uint32_t color=pixels[y*300+x];
    if(color==0xff777777)++ink;
    else if(color!=0xff282828)++antialias;
  }
  assert(ink>0&&antialias>0); /* proportional font with smooth edge coverage */
  for(int y=1;y<8;++y)for(int x=1;x<CSD_MENU_WIDTH-1;++x)
    assert(pixels[y*300+x]==0xff282828); /* no glyphs wrap into top padding */
  for(int y=0;y<300;++y)for(int x=CSD_MENU_WIDTH;x<300;++x)
    assert(pixels[y*300+x]==0); /* menu text never spills into the app */
  csd_menu_paint(pixels,300,300);
  ink=0;
  for(int y=8;y<30;++y)for(int x=12;x<110;++x)
    if(pixels[y*300+x]==0xff777777)++ink;
  assert(ink>0); /* disabled Edit keymap retains its muted text color */
  /* Tiny windows clip long labels without wrapping pixels into other rows. */
  uint32_t narrow[160*300]={0};
  csd_menu_paint(narrow,160,300);
  ink=0;
  for(int y=8;y<30;++y)for(int x=12;x<90;++x)
    if(narrow[y*160+x]==0xff777777)++ink;
  assert(ink>0);
  uint32_t clipped[300*40]={0};
  menu_text(clipped,300,40,40,12,24,"Long menu label",0xffdedede,14);
  for(int y=0;y<40;++y)for(int x=40;x<300;++x)assert(clipped[y*300+x]==0);
  assert(narrow[300*160-1]==0);
  uint32_t expanded[500*160]={0};
  f.menu_submenu=1;csd_menu_paint(expanded,500,160);
  assert(expanded[40*500+210]!=0&&expanded[110*500+210]==0);
  assert(expanded[10*500+210]==0); /* submenu aligns with Resize row */
  uint32_t compact[160*160]={0};csd_menu_paint(compact,160,160);
  assert(compact[80*160+10]!=0&&compact[110*160+10]==0);
  f.menu_submenu=0;
  /* Missing/ambiguous ownership or an active editor disables keymap. */
  assert(!csd_menu_enabled(&f,0));
  task_count=2;tasks[1]=tasks[0];assert(!csd_menu_enabled(&f,0));task_count=1;
  struct keymap_overlay overlay={0};s.overlay=&overlay;assert(!csd_menu_enabled(&f,0));s.overlay=0;
  /* Header keymap uses the same task-scoped worker request as F2. */
  assert(!setenv("ANVILDROID_RUNTIME_GENERATION","0123456789abcdef0123456789abcdef",1));
  keymap_display=(struct wl_display *)1;keymap_sync_proxy=&top_proxy;
  keymap_event_queue=(struct wl_event_queue *)1;keymap_scheduled=1;
  f.menu_open=1;
  assert(csd_menu_enabled(&f,0));csd_menu_activate(&f,0);
  assert(!f.menu_open&&keymap_shortcut_task==7&&!strcmp(keymap_shortcut_package,"com.test"));
  keymap_display=0;keymap_sync_proxy=0;keymap_event_queue=0;keymap_scheduled=0;
  assert(!s.resize.width&&!s.resize.height&&!s.preview_width&&!s.preview_height);
  assert(!csd_menu_enabled(&f,1)); /* no viewporter: cannot scale safely */
  struct object viewport={0};s.viewport=&viewport;scale_has_viewporter=1;
  assert(csd_menu_enabled(&f,1)&&!csd_menu_enabled(&f,2));
  f.menu_open=1;f.menu.width=800;f.menu.height=600;
  csd_menu_activate(&f,1);
  assert(f.menu_open&&f.menu_submenu&&!s.resize.width);
  assert(csd_menu_enabled(&f,2)&&csd_menu_enabled(&f,3));
  s.resize.width=700;s.resize.height=500;
  f.menu_open=1;csd_menu_activate(&f,3);
  assert(resize_mode_fixed(s.app_id)&&!s.resize.width&&!s.resize.height&&!f.menu_open);
  s.fit_w=800;s.fit_h=600;
  f.menu_open=1;f.menu_submenu=1;csd_menu_activate(&f,2);
  assert(!resize_mode_fixed(s.app_id)&&s.resize.width==800&&s.resize.height==600);
  assert(!resize_mode_fixed("com.shg.sg451"));
  assert(!resize_mode_fixed("com.shg.sg432"));
  s.app_id="com.shg.sg451";s.resize.width=s.resize.height=0;
  f.menu_open=1;f.menu_submenu=1;
  assert(csd_menu_enabled(&f,2)&&csd_menu_enabled(&f,3));
  csd_menu_activate(&f,3);
  assert(resize_mode_fixed(s.app_id)&&!s.resize.width);
  f.menu_open=1;f.menu_submenu=1;csd_menu_activate(&f,2);
  assert(!resize_mode_fixed(s.app_id)&&s.resize.width==800&&s.resize.height==600);
  s.app_id="com.test";s.resize.width=s.resize.height=0;f.menu_submenu=0;
  /* Escape consumes input, including the subsequent release after close. */
  f.menu_open=1;struct object keyboard={.keyboard_focus=&s};
  assert(csd_menu_key(&keyboard,1,1)&&!f.menu_open);
  assert(keyboard.keymap_blocked_keys[0]&(1u<<1));
  f.menu_open=1;f.menu_submenu=1;
  assert(csd_menu_key(&keyboard,1,1)&&f.menu_open&&!f.menu_submenu);
  assert(csd_menu_key(&keyboard,106,1)&&f.menu_submenu&&f.menu_hover==2);
  assert(csd_menu_key(&keyboard,108,1)&&f.menu_hover==3);
  assert(csd_menu_key(&keyboard,105,1)&&!f.menu_submenu&&f.menu_hover==1);
  assert(csd_menu_key(&keyboard,1,1)&&!f.menu_open);
  /* Size opens independently of the resize policy and changes both the host
   * frame and the deferred Android task size in layout mode. */
  f.menu.width=800;f.menu.height=600;f.menu_open=1;
  csd_menu_activate(&f,CSD_MENU_SIZE);
  assert(f.menu_submenu==2&&f.menu_hover==CSD_MENU_PRESET_FIRST);
  assert(csd_menu_row_at(210,68,800,600,2)==CSD_MENU_PRESET_FIRST);
  assert(csd_menu_row_at(210,228,800,600,2)==CSD_MENU_PORTRAIT);
  csd_menu_activate(&f,CSD_MENU_PRESET_FIRST);
  assert(!f.menu_open&&s.fit_w==480&&s.fit_h==800);
  assert(s.resize.width==480&&s.resize.height==800&&s.resize.dirty);
  assert(s.profile_loaded&&s.profile_dirty&&s.profile_state_dirty);
  assert(f.restore_width==480&&f.restore_height==800);
  f.menu_open=1;f.menu_submenu=2;
  csd_menu_activate(&f,CSD_MENU_LANDSCAPE);
  assert(s.fit_w==800&&s.fit_h==480&&s.resize.width==800&&s.resize.height==480);
  f.menu_open=1;f.menu_submenu=2;
  csd_menu_activate(&f,CSD_MENU_PORTRAIT);
  assert(s.fit_w==480&&s.fit_h==800&&s.resize.width==480&&s.resize.height==800);
  /* Keep resolution must never queue Android relayout from Size/Rotate. */
  resize_mode_set(s.app_id,1);
  f.menu_open=1;f.menu_submenu=2;
  csd_menu_activate(&f,CSD_MENU_PRESET_LAST);
  assert(resize_mode_fixed(s.app_id)&&s.fit_w==1280&&s.fit_h==720&&!s.resize.width);
  f.menu_open=1;f.menu_submenu=2;csd_menu_activate(&f,CSD_MENU_PORTRAIT);
  assert(s.fit_w==720&&s.fit_h==1280&&!s.resize.width);
  resize_mode_set(s.app_id,0);
  /* Reject invalid bounds and disabled entries without touching the task. */
  csd_menu_size(&f,0,800);assert(s.fit_w==720&&s.fit_h==1280);
  s.resize.dragging=1;f.menu_open=1;f.menu_submenu=2;
  csd_menu_activate(&f,CSD_MENU_PRESET_FIRST);assert(s.fit_w==720&&f.menu_open);
  s.resize.dragging=0;
  /* Small menus scroll by wheel and keyboard; back/Escape return to Size. */
  f.menu.width=160;f.menu.height=100;f.menu_scroll=0;
  struct object scroller={.menu_focus=&f,.csd_x=10,.csd_y=40};
  csd_menu_scroll(&scroller,1);assert(f.menu_scroll==1);
  assert(csd_menu_row_at_offset(10,40,160,100,2,f.menu_scroll)==CSD_MENU_PRESET_FIRST+1);
  f.menu_hover=CSD_MENU_PRESET_FIRST;f.menu_scroll=0;
  assert(csd_menu_key(&keyboard,103,1)&&f.menu_hover==CSD_MENU_CUSTOM&&f.menu_scroll==6);
  assert(csd_menu_key(&keyboard,1,1)&&!f.menu_submenu&&f.menu_hover==CSD_MENU_SIZE);
  assert(csd_menu_key(&keyboard,106,1)&&f.menu_submenu==2&&f.menu_scroll==0);
  assert(csd_menu_key(&keyboard,105,1)&&!f.menu_submenu&&f.menu_hover==CSD_MENU_SIZE);
  /* Paint a clipped, scrolled submenu with its true surface stride. */
  uint32_t small_size[160*100]={0};f.menu_submenu=2;f.menu_scroll=5;
  csd_menu_paint(small_size,160,100);
  assert(small_size[40*160+10]!=0&&small_size[99*160+159]==0);
  f.menu_scroll=0;f.menu_submenu=0;
  /* Unmaximize must not replace the chosen preset with old normal bounds. */
  f.maximized=1;f.menu_open=1;f.menu_submenu=2;f.menu.width=800;f.menu.height=600;
  csd_menu_activate(&f,CSD_MENU_PRESET_FIRST);
  assert(f.size_pending&&!f.maximized&&f.restore_width==480);
  uint32_t max_state=1;
  struct {size_t size,alloc;uint32_t *data;} states={.size=sizeof(max_state),.data=&max_state};
  configure(&top,0,1920,1080,&states);
  assert(f.size_pending&&s.fit_w==480&&f.restore_width==480);
  states.size=0;configure(&top,0,800,600,&states);
  assert(!f.size_pending&&s.resize.width==480&&s.resize.height==800);
  assert(f.restore_width==480&&f.restore_height==800);
  f.menu_submenu=0;
  /* Every panel clips to the actual surface, including tiny restored frames
   * and a submenu that becomes larger after it was scrolled. */
  const int widths[]={1,64,160,300,430,500},heights[]={1,64,100,232,264,300};
  for(unsigned wi=0;wi<sizeof(widths)/sizeof(widths[0]);++wi)
    for(unsigned hi=0;hi<sizeof(heights)/sizeof(heights[0]);++hi)
      for(int submenu=0;submenu<3;++submenu) {
        int w=widths[wi],h=heights[hi];
        uint32_t *guarded=calloc((size_t)w*h+2,sizeof(*guarded));assert(guarded);
        guarded[0]=guarded[w*h+1]=0x12345678;
        f.menu_submenu=submenu;f.menu_scroll=submenu==2?5:0;
        csd_menu_paint(guarded+1,w,h);
        assert(guarded[0]==0x12345678&&guarded[w*h+1]==0x12345678);
        if(submenu)assert(f.menu_scroll+csd_menu_visible(w,h,submenu)<=csd_menu_count(submenu));
        free(guarded);
      }
  f.menu_submenu=f.menu_scroll=0;
  struct object pointer={.menu_focus=&f};
  /* Outside click dismisses, a drag from outside into a row never activates. */
  f.menu_open=1;f.menu.width=800;f.menu.height=600;
  pointer.csd_x=500;pointer.csd_y=100;
  csd_menu_button(&pointer,0x110,1);csd_menu_button(&pointer,0x110,0);
  assert(!f.menu_open&&commits>0);
  /* Reproduce unordered bars: menu above the last bar must not end up
   * underneath the left bar, including detached top/bottom bar siblings. */
  struct game_fit_state fit={0};s.fit=&fit;
  struct object layer={.p=&stack_sub[0],.protocol_name="wl_subsurface",.parent=&s};
  struct object content={.p=&stack_surface[0]};layer.surface=&content;
  s.next=&layer;
  for(int i=0;i<4;++i)fit.bars[i]=(struct csd_piece){
    .surface=&stack_surface[i+1],.subsurface=&stack_sub[i+1]};
  f.menu=(struct csd_piece){.surface=&stack_surface[5],.subsurface=&stack_sub[5],.width=800,.height=600};
  f.menu_open=1;f.menu_x=f.menu_y=0;
  for(int i=0;i<6;++i)stack_order[i]=i;
  csd_compositor=csd_subcompositor=csd_shm=&root;
  stack_enabled=1;
  csd_menu_sync(&f,0,0,800,600);
  assert(stack_position(5)==5);
  /* A subsequent fit frame can lift a bar above the menu again. */
  union argument above_menu={.o=&stack_surface[5]};
  stack_request(&stack_sub[3],2,&above_menu);
  assert(stack_position(3)>stack_position(5));
  csd_menu_sync(&f,0,0,800,600);assert(stack_position(5)==5);
  /* Full Android UI owns a separate set of letterbox surfaces on the CSD
   * frame, with no game_fit_state. Its side bars must not cover the menu or
   * intercept clicks, including when csd_sync restacks them on later frames. */
  s.fit=0;s.full_ui=1;s.app=0;s.viewport=0;
  s.full_w=800;s.full_h=600;s.full_canvas_w=720;s.full_canvas_h=1280;
  for(int i=0;i<4;++i)f.bars[i]=fit.bars[i];
  stack_order[0]=0;stack_order[1]=5;
  for(int i=0;i<4;++i)stack_order[i+2]=i+1;
  csd_menu_sync(&f,0,0,800,600);
  assert(stack_position(5)==5);
  for(int frame=0;frame<3;++frame) {
    csd_sync(&s,0,0,800,600);
    assert(f.menu_open&&stack_position(5)==5);
  }
  stack_enabled=0;
  /* Default Full Android UI exposes Size even without a package/task viewport. */
  s.full_ui=1;s.app=0;s.viewport=0;s.full_w=1280;s.full_h=720;
  f.menu_submenu=0;f.menu_scroll=0;f.menu.width=800;f.menu.height=600;
  assert(csd_menu_row(&f,10,68,800,600)==CSD_MENU_SIZE);
  assert(csd_menu_enabled(&f,CSD_MENU_SIZE)&&!csd_menu_enabled(&f,CSD_MENU_RESIZE));
  f.menu_open=1;csd_menu_activate(&f,CSD_MENU_SIZE);
  assert(f.menu_submenu==2&&csd_menu_enabled(&f,CSD_MENU_PRESET_FIRST));
  assert(csd_menu_row(&f,210,68,800,600)==CSD_MENU_PRESET_FIRST);
  f.menu_open=1;f.menu_submenu=2;
  assert(csd_menu_enabled(&f,CSD_MENU_CUSTOM));
  csd_menu_activate(&f,CSD_MENU_CUSTOM);
  assert(s.custom_size.enabled&&s.custom_size.w==1280&&s.custom_size.h==720&&!f.menu_open);
  return 0;
}
