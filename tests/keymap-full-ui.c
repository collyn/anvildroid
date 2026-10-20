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
  /* A killed native read lock recovers without opening the scheme library. */
  const char *lock_generation="01234567890123456789012345678901";
  char lock_owner[128];
  for(int journal=0;journal<2;++journal){
    assert(!mkdir("keymaps/.scheme-lock",0700));
    int lock_fd=open("keymaps/.scheme-lock/pid",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
    int lock_n=snprintf(lock_owner,sizeof(lock_owner),"2147483647 1 %s\n",lock_generation);
    assert(lock_fd>=0&&write(lock_fd,lock_owner,(unsigned long)lock_n)==lock_n);close(lock_fd);
    if(journal){
      lock_fd=open("keymaps/.scheme-lock/ready",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);assert(lock_fd>=0);close(lock_fd);
      assert(!keymap_scheme_lock(lock_generation)); /* journal remains untouched */
      lock_fd=open("keymaps/.scheme-lock/pid",PROFILE_NOFOLLOW|PROFILE_CLOEXEC);assert(lock_fd>=0);close(lock_fd);
      assert(!unlinkat(-100,"keymaps/.scheme-lock/ready",0));
    }
    assert(keymap_scheme_lock(lock_generation)); /* stale PID-only lock */
    assert(!keymap_scheme_lock(lock_generation)); /* live owner stays locked */
    keymap_scheme_unlock();
  }
  /* F2's marker path also reaps a stale lock when no marker existed. */
  assert(!mkdir("keymaps/.scheme-lock",0700));
  int lock_fd=open("keymaps/.scheme-lock/pid",1|PROFILE_CREAT|PROFILE_EXCL|PROFILE_NOFOLLOW|PROFILE_CLOEXEC,0600);
  int lock_n=snprintf(lock_owner,sizeof(lock_owner),"2147483647 1 %s\n",lock_generation);
  assert(lock_fd>=0&&write(lock_fd,lock_owner,(unsigned long)lock_n)==lock_n);close(lock_fd);
  assert(keymap_marker_begin(lock_generation,"org.test.app"));keymap_marker_end();
  /* Native editor first-open recovers stale sessions without a GUI mutation. */
  const char *generation="01234567890123456789012345678901";
  char process_start[32],session[352];assert(keymap_process_start(getpid(),process_start,sizeof(process_start))==1);
  assert(keymap_marker_begin(generation,"org.test.app"));
  assert(!keymap_marker_begin(generation,"org.test.app")); /* live session */
  keymap_marker_end();
  snprintf(session,sizeof(session),"old-generation org.test.app %d %s\n",getpid(),process_start);
  assert(keymap_worker_file("keymap-editing",session));
  assert(keymap_marker_begin(generation,"org.test.app"));keymap_marker_end();
  snprintf(session,sizeof(session),"%s org.test.app 2147483647 1\n",generation);
  assert(keymap_worker_file("keymap-editing",session));
  assert(keymap_marker_begin(generation,"org.test.app"));keymap_marker_end();
  snprintf(session,sizeof(session),"%s org.test.app %d 0\n",generation,getpid());
  assert(keymap_worker_file("keymap-editing",session));
  assert(keymap_marker_begin(generation,"org.test.app"));keymap_marker_end(); /* reused PID */
  snprintf(session,sizeof(session),"%s org.test.app %d\n",generation,getpid());
  assert(keymap_worker_file("keymap-editing",session));
  assert(!keymap_marker_begin(generation,"org.test.app"));keymap_marker_end(); /* old live marker */
  assert(keymap_worker_file("keymap-editing","malformed\n"));
  assert(!keymap_marker_begin(generation,"org.test.app"));keymap_marker_end();
  /* Scheme selection reloads the existing app, but only after its lock exits. */
  assert(keymap_worker_file("keymap-scheme-reload","01234567890123456789012345678901 org.other.app reload-1\n"));
  assert(!mkdir("keymaps/.scheme-lock",0700));keymap_io_poll();
  assert(canvas.hints&&!canvas.hints->close_pending);
  assert(!rmdir("keymaps/.scheme-lock"));keymap_io_poll();
  assert(canvas.hints&&canvas.hints->close_pending);
  keymap_overlay_sync(&canvas,50,80,900,540);assert(!canvas.hints);
  keymap_io_poll();assert(canvas.hints&&canvas.hints->model.points[0].key==30);
  /* A library mutation lock refuses an editor before loading any draft. */
  assert(!mkdir("keymaps/.scheme-lock",0700));
  frame.menu_open=1;csd_menu_activate(&frame,0);keymap_io_poll();
  assert(!canvas.overlay);
  int marker=open("keymap-editing",PROFILE_NOFOLLOW|PROFILE_CLOEXEC);assert(marker<0);
  assert(!rmdir("keymaps/.scheme-lock"));
  /* Cancel and a Save after Undo preserve exact imported V1 bytes. */
  const char legacy[]="ANVIL_KEYMAP 1 default org.test.app 1\n30 12345 23456 1\n";
  int legacy_fd=open("keymaps/org.test.app",1|01000|PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
  assert(legacy_fd>=0&&write(legacy_fd,legacy,sizeof(legacy)-1)==sizeof(legacy)-1);close(legacy_fd);
  for(int save=0;save<2;++save) {
    frame.menu_open=1;csd_menu_activate(&frame,0);keymap_io_poll();assert(canvas.overlay);
    struct keymap_overlay *edit=canvas.overlay;edit->model.selected=0;
    keymap_history_init(edit);edit->model.points[0].nx+=1000;edit->model.dirty=1;keymap_history_commit(edit);
    if(save){keymap_history_step(edit,-1);assert(!edit->model.dirty);edit->save_pending=1;keymap_io_poll();assert(edit->close_result==2);}
    else{edit->close_pending=1;edit->close_result=3;}
    keymap_overlay_destroy(&canvas);keymap_io_poll();
    char bytes[128]={0};legacy_fd=open("keymaps/org.test.app",PROFILE_NOFOLLOW|PROFILE_CLOEXEC);
    assert(legacy_fd>=0&&read(legacy_fd,bytes,sizeof(bytes))==sizeof(legacy)-1);close(legacy_fd);
    assert(!memcmp(bytes,legacy,sizeof(legacy)-1));
  }
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
  keymap_io_poll();
  assert(!unlinkat(-100,"keymap-scheme-protocol",0));
  assert(!chdir("/tmp"));assert(!rmdir(path));
  return 0;
}
