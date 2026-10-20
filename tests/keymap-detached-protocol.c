#define ANVIL_KEYMAP_EDITOR 1
#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int level,const char *tag,const char *format,...) {(void)level;(void)tag;(void)format;return 0;}
static const struct interface surface_i={.name="wl_surface"},xdg_i={.name="xdg_surface"},
  top_i={.name="xdg_toplevel"},wm_i={.name="xdg_wm_base"},pool_i={.name="wl_shm_pool"},
  buffer_i={.name="wl_buffer"},region_i={.name="wl_region"};
static struct proxy compositor={&surface_i},wm={&wm_i},shm={&surface_i},pool={&pool_i},region={&region_i},
  panel_surface={&surface_i},panel_xdg={&xdg_i},panel_top={&top_i},android_top={&top_i},buffers[16],seat={&surface_i};
static struct proxy panel_decoration={&decoration_interface};
static int attached,commits,ack_at,attach_at,step,fail_constructor,fail_listener,wm_listened;
static int destroyed[16],destroyed_count,queued,buffer_count,moves;
static int requested_decoration,parent_requests;
static char app_id[512];
static uint32_t version(struct proxy *p){(void)p;return 1;}
static int listen_to(struct proxy *p,void (**events)(void),void *data) {
  (void)events;(void)data;if(p==&wm)++wm_listened;
  return (p==&panel_xdg&&fail_listener==1)||(p==&panel_top&&fail_listener==2)?-1:0;
}
static void queue(struct proxy *p,struct wl_event_queue *q) {
  assert(!q);if(p==&panel_surface||p==&panel_xdg||p==&panel_top)++queued;
}
static void destroyed_proxy(struct proxy *p){(void)p;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *type,uint32_t v) {
  (void)v;++step;
  if(type==&surface_i)return fail_constructor==1?0:&panel_surface;
  if(type==&xdg_i){assert(p==&wm&&op==2&&a[1].o==&panel_surface);return fail_constructor==2?0:&panel_xdg;}
  if(type==&top_i){assert(p==&panel_xdg&&op==1);return fail_constructor==3?0:&panel_top;}
  if(type==&decoration_interface)return &panel_decoration;
  if(type==&region_i)return &region;
  if(type==&pool_i)return &pool;
  if(type==&buffer_i){assert(buffer_count<16);buffers[buffer_count].interface=&buffer_i;return &buffers[buffer_count++];}
  if(p==&panel_xdg&&op==4){assert(a[0].u==91);ack_at=step;}
  if(p==&panel_surface&&op==1&&a[0].o){assert(ack_at);++attached;attach_at=step;}
  if(p==&panel_surface&&op==6)++commits;
  if(p==&panel_top&&op==3)snprintf(app_id,sizeof(app_id),"%s",a[0].s);
  if(p==&panel_top&&op==1&&!type)++parent_requests;
  if(p==&panel_top&&op==5){assert(a[0].o==&seat&&a[1].u==123);++moves;}
  if(p==&panel_decoration&&op==1)requested_decoration=(int)a[0].u;
  if(op==0&&!type&&(p==&panel_top||p==&panel_xdg||p==&panel_surface||p==&panel_decoration))
    destroyed[destroyed_count++]=p==&panel_decoration?4:p==&panel_top?3:p==&panel_xdg?2:1;
  return 0;
}
static void reset(void) {
  attached=commits=ack_at=attach_at=step=destroyed_count=queued=0;
  fail_constructor=fail_listener=0;
}
int main(void) {
  real_marshal=record;real_destroy=destroyed_proxy;real_listen=listen_to;get_version=version;real_set_queue=queue;
  csd_compositor=&compositor;csd_xdg_wm=&wm;csd_shm=&shm;
  csd_surface_i=&surface_i;csd_xdg_surface_i=&xdg_i;csd_xdg_top_i=&top_i;
  csd_region_i=&region_i;csd_pool_i=&pool_i;csd_buffer_i=&buffer_i;
  struct keymap_overlay f={.piece={.width=1000,.height=700}};
  snprintf(f.package,sizeof(f.package),"org.anvildroid.touchprobe");
  struct object owner={.app=1,.app_id=f.package,.overlay=&f},pointer={0},seat_object={.p=&seat};
  /* Supply a real tracked Android top-level so this test catches an attempt
   * to turn the tools into a transient and trap canvas keyboard activation. */
  struct object app_top={.p=&android_top,.protocol_name="xdg_toplevel",.surface=&owner};
  objects=&owner;owner.next=&app_top;app_top.next=&pointer;pointer.parent=&seat_object;
  assert(object_is(&app_top,"xdg_toplevel")&&app_top.surface==&owner);
  assert(keymap_add(&f.model,250,350,1000,700));assert(keymap_assign(&f.model,44));
  struct keymap_model draft=f.model;
  assert(keymap_panel_create(&owner));
  assert(!parent_requests);
  assert(f.detached&&!f.panel.configured&&!attached&&commits==1&&queued==3&&!wm_listened);
  assert(strstr(app_id,".org.anvildroid.touchprobe.keymap-editor"));
  keymap_panel_size(&f,&panel_top,400,800,0);
  assert(!attached);keymap_panel_configure(&f,&panel_xdg,91);
  assert(f.panel.configured&&attached==1&&ack_at<attach_at&&f.panel.piece.width==400&&f.panel.piece.height==800);
  assert(keymap_panel_owner(&panel_surface)==&owner);
  pointer.pointer_surface=&panel_surface;pointer.csd_x=20;pointer.csd_y=12;
  assert(keymap_panel_button(&pointer,123,272,1)&&moves==1);
  assert(keymap_panel_button(&pointer,124,272,0)&&moves==1);
  keymap_panel_close(&f,&panel_top);
  assert(f.panel.dock_pending&&f.detached&&!destroyed_count);
  assert(!memcmp(&draft,&f.model,sizeof(draft)));
  /* Dock is processed through normal repaint/lifecycle; it does not Save or
   * Cancel the draft and destroys top-level roles before their wl_surface. */
  csd_compositor=0;keymap_overlay_sync(&owner,0,0,1000,700);csd_compositor=&compositor;
  assert(!f.detached&&!f.panel.piece.surface&&destroyed_count==3);
  assert(destroyed[0]==3&&destroyed[1]==2&&destroyed[2]==1&&!memcmp(&draft,&f.model,sizeof(draft)));
  for(int failure=1;failure<=3;++failure){
    reset();fail_constructor=failure;assert(!keymap_panel_create(&owner));
    assert(!f.detached&&!f.panel.top&&!f.panel.xdg&&!f.panel.piece.surface);
    assert(destroyed_count==failure-1);
  }
  for(int failure=1;failure<=2;++failure){
    reset();fail_listener=failure;assert(!keymap_panel_create(&owner));
    assert(!f.detached&&destroyed_count==failure+1);
    assert(destroyed[destroyed_count-1]==1);
  }
  /* Task destruction tears down both editor surfaces through their owner. */
  reset();owner.overlay=calloc(1,sizeof(f));assert(owner.overlay);*owner.overlay=f;
  owner.overlay->panel=(__typeof__(f.panel)){0};
  assert(keymap_panel_create(&owner));keymap_overlay_destroy(&owner);
  assert(!owner.overlay&&destroyed_count==3&&destroyed[0]==3&&destroyed[2]==1);
  reset();owner.overlay=calloc(1,sizeof(f));assert(owner.overlay);*owner.overlay=f;
  owner.overlay->panel=(__typeof__(f.panel)){0};
  assert(keymap_panel_create(&owner));keymap_connection_lost();
  assert(!owner.overlay&&destroyed_count==3&&destroyed[0]==3&&destroyed[2]==1);
  /* Ask for CSD explicitly when xdg-decoration exists. A compositor that
   * chooses SSD gets no duplicate client header after its configure ack. */
  reset();owner.overlay=&f;
  struct decoration_binding binding={.manager=&wm};decoration_bindings=&binding;
  assert(keymap_panel_create(&owner)&&requested_decoration==1&&keymap_panel_header(&f)==33);
  keymap_panel_decoration(&f,&panel_decoration,2);assert(keymap_panel_header(&f)==33);
  keymap_panel_configure(&f,&panel_xdg,91);assert(keymap_panel_header(&f)==0);
  keymap_panel_destroy(&f);assert(destroyed_count==4&&destroyed[0]==4&&destroyed[3]==1);
  decoration_bindings=0;
  assert(!parent_requests);
  while(csd_buffers){struct csd_buffer *old=csd_buffers;csd_buffers=old->next;free(old);}
  objects=0;return 0;
}
