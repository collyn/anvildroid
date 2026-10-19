#define KEYMAP_BASE "."
#define main lifecycle_main
#include "display-lifecycle.c"
#undef main
extern char *mkdtemp(char *);
extern int chdir(const char *);
extern int setenv(const char *,const char *,int);
extern int rmdir(const char *);
static struct proxy *canvas_marshal(struct proxy *p,uint32_t op,union argument *a,
                                   const struct interface *i,uint32_t v) {
  return p->interface==&display_i?marshal(p,op,a,i,v):0;
}
static void key(void *d,struct proxy *p,uint32_t a,uint32_t b,uint32_t k,uint32_t s) {
  (void)d;(void)p;(void)a;(void)b;(void)k;(void)s;
}
int main(void) {
  char path[]="/tmp/anvil-full-keymap-XXXXXX";assert(mkdtemp(path));assert(!chdir(path));
  setenv("ANVILDROID_RUNTIME_ID","default",1);
  setenv("ANVILDROID_RUNTIME_GENERATION","01234567890123456789012345678901",1);
  setup();real_marshal=canvas_marshal;wl_display_connect(0);
  struct proxy root={0},top_proxy={0};
  struct object canvas={.p=&root,.full_ui=1,.app_id="Waydroid",
    .full_canvas_w=2000,.full_canvas_h=1200,.full_w=1000,.full_h=700};
  struct object top={.p=&top_proxy,.surface=&canvas};
  struct csd_frame frame={.top=&top};canvas.csd=&frame;
  void(*listeners[])(void)={0,0,0,(void(*)(void))key};
  struct object keyboard={.keyboard_focus=&canvas,.listener=listeners};
  canvas.next=&keyboard;objects=&canvas;
  tasks[0]=(struct task){.id=1,.package="com.android.launcher3",.width=2000,.height=1200};
  task_count=1;
  assert(csd_frame_hit(&frame,0,18,18,1000,33)==CSD_MENU);
  assert(keymap_task_owner(&canvas)==1&&csd_menu_enabled(&frame,0));
  tasks[1]=(struct task){.id=7,.package="org.test.app",.x=100,.y=60,.width=1800,.height=1080};
  task_count=2;
  assert(keymap_task_owner(&canvas)==7&&!strcmp(keymap_package(&canvas),"org.test.app"));
  assert(csd_menu_enabled(&frame,0)&&!csd_menu_enabled(&frame,1));
  /* Disabled Resize remains a visible menu row; only activation is blocked. */
  assert(csd_menu_row(&frame,10,40,1000,700)==1);
  assert(!csd_menu_enabled(&frame,1));
  keymap_canvas_bounds(&canvas);
  assert(canvas.bx==50&&canvas.by==80&&canvas.bw==900&&canvas.bh==540);
  assert(keymap_owner(&root)==&canvas);
  frame.menu_open=1;csd_menu_activate(&frame,0);
  assert(!frame.menu_open&&keymap_shortcut_task==7);
  keymap_io_poll();assert(canvas.overlay&&canvas.overlay->task==7);
  assert(!strcmp(canvas.overlay->package,"org.test.app")&&!strcmp(canvas.app_id,"Waydroid"));
  assert(keymap_add(&canvas.overlay->model,450,270,900,540));
  assert(keymap_assign(&canvas.overlay->model,30));
  canvas.overlay->save_pending=1;keymap_io_poll();
  assert(canvas.overlay->close_pending&&canvas.overlay->close_result==2);
  keymap_overlay_destroy(&canvas);
  keymap_io_poll();assert(canvas.hints&&canvas.hints->task==7);
  assert(!strcmp(canvas.hints->package,"org.test.app"));
  int x,y,w,h;assert(map_task(&canvas,&x,&y,&w,&h));
  assert(x==100&&y==60&&w==1800&&h==1080);
  /* App switch cancels the editor, never saves bindings to the new app. */
  keymap_key(&keyboard,0,1,1,60,1);keymap_key(&keyboard,0,1,1,60,0);
  keymap_io_poll();assert(canvas.overlay);
  tasks[1].id=8;snprintf(tasks[1].package,sizeof(tasks[1].package),"org.other.app");
  keymap_io_poll();assert(canvas.overlay->close_pending&&canvas.overlay->close_result==3);
  keymap_overlay_destroy(&canvas);keymap_hints_destroy(&canvas);
  /* Ambiguous visible apps and out-of-canvas bounds fail closed. */
  tasks[2]=tasks[1];tasks[2].id=9;task_count=3;
  assert(!keymap_task_owner(&canvas)&&!csd_menu_enabled(&frame,0));
  task_count=2;tasks[1].x=1900;assert(!keymap_task_owner(&canvas));
  task_count=1;keymap_canvas_bounds(&canvas);
  assert(canvas.has_bounds&&canvas.bx==0&&canvas.by==50&&canvas.bw==1000&&canvas.bh==600);
  assert(keymap_task_owner(&canvas)==1);
  frame.menu_open=1;csd_menu_activate(&frame,0);
  keymap_io_poll();assert(canvas.overlay&&canvas.overlay->task==1);
  assert(!strcmp(canvas.overlay->package,"com.android.launcher3"));
  keymap_overlay_destroy(&canvas);
  task_count=0;keymap_canvas_bounds(&canvas);assert(!canvas.has_bounds);
  objects=0;
  assert(!unlinkat(-100,"keymaps/org.test.app",0));assert(!rmdir("keymaps"));
  assert(!chdir("/tmp"));assert(!rmdir(path));
  return 0;
}
