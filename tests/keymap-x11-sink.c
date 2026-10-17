/* Original deterministic guest-side fixture: production bridge event handlers,
 * authenticated-sink socket mock. No Android attachment or runtime services. */
#define wl_display_connect fixture_wl_display_connect
#define wl_display_connect_to_fd fixture_wl_display_connect_to_fd
#define wl_display_disconnect fixture_wl_display_disconnect
#define wl_display_dispatch fixture_wl_display_dispatch
#define wl_display_dispatch_pending fixture_wl_display_dispatch_pending
#define wl_display_dispatch_queue fixture_wl_display_dispatch_queue
#define wl_display_dispatch_queue_pending fixture_wl_display_dispatch_queue_pending
#define wl_proxy_add_listener fixture_wl_proxy_add_listener
#define wl_proxy_create_wrapper fixture_wl_proxy_create_wrapper
#define wl_proxy_destroy fixture_wl_proxy_destroy
#define wl_proxy_marshal_array_constructor_versioned fixture_wl_proxy_marshal_array_constructor_versioned
#define wl_proxy_marshal_flags fixture_wl_proxy_marshal_flags
#define wl_proxy_wrapper_destroy fixture_wl_proxy_wrapper_destroy
#define main mapping_baseline_main
#include "scrcpy-mapping.c"
#undef main
static struct object fixture_app, fixture_pointer, fixture_keyboard;
static struct proxy fixture_surface;
static struct keymap_overlay fixture_hints;
static int forwarded;
static void key_forward(void *d,struct proxy *p,uint32_t serial,uint32_t time,uint32_t key,uint32_t state) {
  (void)d;(void)p;(void)serial;(void)time;(void)key;(void)state;++forwarded;
}
static void motion_forward(void *d,struct proxy *p,uint32_t time,int32_t x,int32_t y) {
  (void)d;(void)p;(void)time;(void)x;(void)y;++forwarded;
}
static void button_forward(void *d,struct proxy *p,uint32_t serial,uint32_t time,uint32_t button,uint32_t state) {
  (void)d;(void)p;(void)serial;(void)time;(void)button;(void)state;++forwarded;
}
static void (*key_listeners[])(void)={0,0,0,(void(*)(void))key_forward};
static void (*pointer_listeners[])(void)={0,0,(void(*)(void))motion_forward,(void(*)(void))button_forward};
void anvil_mapper_setup(int kind) {
  scrcpy_mapping_cancel();map_fd=-1;unavailable=0;packets=0;forwarded=0;
  fixture_hints=(struct keymap_overlay){.passive=1,.task=5,.package="org.test",.model={.count=1,
    .points={{.key=17,.nx=32768,.ny=32768,.kind=kind,.ex=49152,.ey=32768,.duration=100,.repeats=3}}}};
  fixture_app=(struct object){.p=&fixture_surface,.app=1,.has_bounds=1,.app_id="org.test",.bw=300,.bh=200,.hints=&fixture_hints};
  fixture_app.viewport=&fixture_app;
  fixture_pointer=(struct object){.pointer_surface=&fixture_surface,.listener=pointer_listeners};
  fixture_keyboard=(struct object){.keyboard_focus=&fixture_app,.listener=key_listeners};
  fixture_app.next=&fixture_pointer;fixture_pointer.next=&fixture_keyboard;objects=&fixture_app;
  tasks[0]=(struct task){.id=5,.width=300,.height=200,.package="org.test"};task_count=1;
  absolute_pointer_enabled=0;scale_has_viewporter=1;
  assert(keymap_valid(&fixture_hints.model,1));
}
void anvil_mapper_key(unsigned time,unsigned key,unsigned state) {keymap_key(&fixture_keyboard,0,1,time,key,state);}
void anvil_mapper_motion(unsigned time,int x,int y) {csd_pointer_motion(&fixture_pointer,0,time,x,y);}
void anvil_mapper_button(unsigned time,unsigned button,unsigned state) {csd_pointer_button(&fixture_pointer,0,1,time,button,state);}
void anvil_mapper_cancel(void) {keymap_hints_clear(&fixture_app);}
void anvil_mapper_advance(int milliseconds) {map_advanced_advance(milliseconds);}
int anvil_mapper_contacts(void) {return map_count;}
int anvil_mapper_packets(void) {return packets;}
int anvil_mapper_forwarded(void) {return forwarded;}
int anvil_mapper_action(void) {return last[1];}
