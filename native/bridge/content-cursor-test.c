#define ANVIL_KEYMAP_EDITOR 1
#include "bridge.c"
#include <assert.h>

int __android_log_print(int level,const char *tag,const char *format,...) {
  (void)level;(void)tag;(void)format;return 0;
}
static int subsurface_cursor;
int __system_property_get(const char *key,char *value) {
  if(!strcmp(key,"persist.waydroid.cursor_on_subsurface"))
    return snprintf(value,128,"%s",subsurface_cursor?"true":"false");
  value[0]=0;return 0;
}
static struct proxy pointer_proxy,content_proxy,fallback_proxy,guest_proxy,other_proxy;
static struct proxy buffer_proxy,child_proxy,sub_proxy,full_proxy;
static const struct message surface_methods[]={
  {"destroy","",0},{"attach","?oii",0},{"damage","iiii",0},
  {"frame","n",0},{"set_opaque_region","?o",0},{"set_input_region","?o",0},{"commit","",0}};
static const struct message pointer_methods[]={{"set_cursor","u?oii",0}};
static const struct interface surface_i={"wl_surface",1,7,surface_methods,0,0};
static const struct interface pointer_i={"wl_pointer",1,1,pointer_methods,0,0};
static union argument last_cursor[4];
static int requests;
static uint32_t version(struct proxy *p){(void)p;return 1;}
static struct proxy *record(struct proxy *p,uint32_t op,union argument *a,
                            const struct interface *interface,uint32_t v) {
  (void)interface;(void)v;
  if(p==&pointer_proxy) {
    assert(op==0);
    memcpy(last_cursor,a,sizeof(last_cursor));++requests;
  } else assert(p==&guest_proxy&&(op==0||op==1||op==6));
  return 0;
}
static struct proxy *modern(struct proxy *p,uint32_t op,const struct interface *i,
    uint32_t v,uint32_t flags,union argument *a) {
  (void)flags;return record(p,op,a,i,v);
}
static void destroy(struct proxy *p){(void)p;}
static void request(struct object *pointer,struct proxy *surface,int x,int y) {
  union argument a[4]={{.u=pointer->cursor_serial},{.o=surface},{.i=x},{.i=y}};
  bridge_marshal(pointer->p,0,a,0,1,0,0);
}
static void attach(struct proxy *buffer) {
  union argument a[3]={{.o=buffer},{.i=0},{.i=0}};
  bridge_marshal(&guest_proxy,1,a,0,1,0,0);
}
static void commit(int use_modern) {
  bridge_marshal(&guest_proxy,6,0,0,1,0,use_modern);
}
int main(void) {
  real_marshal=record;real_array_flags=modern;get_version=version;real_destroy=destroy;
  pointer_proxy.interface=&pointer_i;guest_proxy.interface=&surface_i;
  struct object *app=track(&content_proxy);app->app=1;
  struct object *guest=track(&guest_proxy);
  struct object *pointer=track(&pointer_proxy);
  pointer->pointer_surface=&content_proxy;pointer->cursor_serial=42;
  pointer->edit_cursor=&fallback_proxy;

  /* Normal app hover must have a cursor even before Android supplies one. */
  csd_cursor_show(pointer);
  assert(requests==1&&last_cursor[1].o==&fallback_proxy);
  request(pointer,0,0,0);
  assert(last_cursor[1].o==&fallback_proxy&&last_cursor[0].u==42);
  /* An HWC cursor surface without a buffer is just as invisible as NULL. */
  request(pointer,&guest_proxy,7,9);
  assert(last_cursor[1].o==&fallback_proxy);
  /* Buffer attachment is pending until commit (legacy and modern ABI). */
  track(&buffer_proxy);attach(&buffer_proxy);
  csd_cursor_show(pointer);assert(last_cursor[1].o==&fallback_proxy);
  commit(0);
  assert(last_cursor[1].o==&guest_proxy&&last_cursor[2].i==7&&last_cursor[3].i==9);
  int before=requests;commit(1);assert(requests==before);
  /* Destroying a buffer proxy leaves the committed cursor image intact. */
  forget_proxy(&buffer_proxy);assert(!guest->attached_buffer&&guest->committed_attached);
  commit(1);assert(requests==before);
  /* Preserve Android's own arrow/text/resize shapes and hotspot. */
  request(pointer,&guest_proxy,7,9);
  assert(last_cursor[1].o==&guest_proxy&&last_cursor[2].i==7&&last_cursor[3].i==9);
  attach(0);before=requests;assert(guest->committed_attached);
  csd_cursor_show(pointer);assert(requests==before);
  commit(1);assert(last_cursor[1].o==&fallback_proxy);
  attach(&buffer_proxy);commit(0);assert(last_cursor[1].o==&guest_proxy);
  /* Cursor destruction must not strand the pointer with a dead surface. */
  forget_proxy(&guest_proxy);
  assert(!pointer->cursor_surface&&last_cursor[1].o==&fallback_proxy);
  /* Relative FPS capture owns hiding; a fallback must never override it. */
  keymap_capture.pointer=pointer;keymap_capture.active=1;
  request(pointer,0,0,0);
  assert(!last_cursor[1].o);
  before=requests;csd_cursor_show(pointer);assert(requests==before);
  keymap_capture_stop();
  assert(last_cursor[1].o==&fallback_proxy);
  /* Cursor frames arriving while locked must stay hidden. Unlock restores
   * the latest drawable guest cursor with its original hotspot. */
  guest=track(&guest_proxy);
  keymap_capture.pointer=pointer;keymap_capture.active=1;
  request(pointer,&guest_proxy,8,10);assert(!last_cursor[1].o);
  before=requests;attach(&buffer_proxy);commit(1);
  assert(requests==before);
  keymap_capture_stop();
  assert(last_cursor[1].o==&guest_proxy&&last_cursor[2].i==8&&last_cursor[3].i==10);
  forget_proxy(&guest_proxy);
  guest=track(&guest_proxy);
  keymap_capture.pointer=pointer;keymap_capture.active=1;
  request(pointer,&guest_proxy,8,10);before=requests;
  forget_proxy(&guest_proxy);assert(requests==before&&!pointer->cursor_surface);
  keymap_capture_stop();assert(last_cursor[1].o==&fallback_proxy);
  /* Unknown non-NULL surfaces are preserved; absence of metadata alone is
   * not evidence that the caller's cursor has no image. */
  request(pointer,&other_proxy,6,4);
  assert(last_cursor[1].o==&other_proxy&&last_cursor[2].i==6&&last_cursor[3].i==4);
  /* Guest cursor-subsurface mode intentionally owns content cursor drawing. */
  subsurface_cursor=1;csd_cursor_subsurface=-1;
  request(pointer,0,0,0);assert(!last_cursor[1].o);
  /* Editor still needs its desktop arrow in that mode. */
  struct keymap_overlay editor={0};app->overlay=&editor;pointer->overlay_focus=app;
  request(pointer,0,0,0);assert(last_cursor[1].o==&fallback_proxy);
  app->overlay=0;pointer->overlay_focus=0;
  subsurface_cursor=0;csd_cursor_subsurface=-1;
  /* Content subsurfaces and the full Android UI use the same policy. */
  struct object *child=track(&child_proxy),*sub=track(&sub_proxy);
  sub->protocol_name="wl_subsurface";sub->surface=child;sub->parent=app;
  pointer->pointer_surface=&child_proxy;
  request(pointer,0,0,0);assert(last_cursor[1].o==&fallback_proxy);
  struct object *full=track(&full_proxy);full->full_ui=1;
  pointer->pointer_surface=&full_proxy;
  request(pointer,0,0,0);assert(last_cursor[1].o==&fallback_proxy);
  /* A wrapped serial is valid, and unrelated/unfocused surfaces stay alone. */
  pointer->cursor_serial=0;request(pointer,0,0,0);
  assert(!last_cursor[0].u&&last_cursor[1].o==&fallback_proxy);
  pointer->pointer_surface=&other_proxy;request(pointer,0,0,0);
  assert(!last_cursor[1].o);
  pointer->pointer_surface=0;before=requests;csd_cursor_show(pointer);
  assert(requests==before);
  pointer->edit_cursor=0;
  while(objects)forget_proxy(objects->p);
  return 0;
}
