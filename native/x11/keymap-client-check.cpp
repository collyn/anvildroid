/* SPDX-License-Identifier: MIT — private real-X11 input -> Wayland wire ->
 * production guest event handlers -> deterministic fake Android touch sink.
 * This is transport/mapper verification, not Android app/game acceptance. */
#define main base_client_main
#include "host-client-check.cpp"
#undef main
#include <X11/extensions/XTest.h>
#include <X11/extensions/Xfixes.h>
extern "C" {
void anvil_mapper_setup(int);
void anvil_mapper_key(unsigned,unsigned,unsigned);
void anvil_mapper_motion(unsigned,int,int);
void anvil_mapper_button(unsigned,unsigned,unsigned);
void anvil_mapper_cancel(void);
void anvil_mapper_advance(int);
int anvil_mapper_contacts(void);
int anvil_mapper_packets(void);
int anvil_mapper_forwarded(void);
int anvil_mapper_action(void);
void anvil_mapper_cursor_bind(wl_compositor*,wl_shm*,wl_surface*,wl_pointer*,uint32_t,
  decltype(&wl_proxy_marshal_array_constructor_versioned),decltype(&wl_proxy_add_listener),
  decltype(&wl_proxy_destroy),decltype(&wl_proxy_get_version));
void anvil_mapper_cursor_request(wl_surface*,int,int);
void anvil_mapper_cursor_frame(wl_surface*,int);
void anvil_mapper_cursor_capture(void);
void anvil_mapper_cursor_release(void);
void anvil_mapper_cursor_finish(void);
}
static uint32_t enter_serial;
static void pump_mapper(Client &c,int count=12) {for(int i=0;i<count;++i){roundtrip(c);usleep(3000);}}
int main(int argc,char **argv) {
  assert(argc==2&&getenv("ANVIL_TEST_X11"));Client c{};c.display=wl_display_connect(argv[1]);assert(c.display);
  auto *registry_proxy=wl_display_get_registry(c.display);
  static const wl_registry_listener registry_events={registry,[](void*,wl_registry*,uint32_t){}};
  wl_registry_add_listener(registry_proxy,&registry_events,&c);
  roundtrip(c);roundtrip(c);
  auto *pointer=wl_seat_get_pointer(c.seat);auto pe=pointer_events;
  pe.enter=[](void *d,wl_pointer *p,uint32_t serial,wl_surface *s,wl_fixed_t x,wl_fixed_t y){
    enter_serial=serial;pointer_events.enter(d,p,serial,s,x,y);
  };
  pe.leave=[](void*,wl_pointer*,uint32_t,wl_surface*){anvil_mapper_cancel();};
  pe.motion=[](void*,wl_pointer*,uint32_t time,wl_fixed_t x,wl_fixed_t y){anvil_mapper_motion(time,x,y);};
  pe.button=[](void*,wl_pointer*,uint32_t,uint32_t time,uint32_t button,uint32_t state){anvil_mapper_button(time,button,state);};
  wl_pointer_add_listener(pointer,&pe,nullptr);
  auto *keyboard=wl_seat_get_keyboard(c.seat);auto ke=keyboard_events;
  ke.leave=[](void*,wl_keyboard*,uint32_t,wl_surface*){anvil_mapper_cancel();};
  ke.key=[](void*,wl_keyboard*,uint32_t,uint32_t time,uint32_t key,uint32_t state){anvil_mapper_key(time,key,state);};
  wl_keyboard_add_listener(keyboard,&ke,nullptr);
  anvil_mapper_setup(0);
  auto a=app(c,"waydroid.anvildroid.mapper-check");roundtrip(c);roundtrip(c);
  auto *content=buffer(c,300,200,0xff206080);xdg_surface_set_window_geometry(a.xdg,0,0,300,200);
  wl_surface_attach(a.surface,content,0,0);wl_surface_commit(a.surface);pump_mapper(c);
  auto *x=XOpenDisplay(nullptr);assert(x);auto w=find_window(x,"waydroid.anvildroid.mapper-check");
  XSetInputFocus(x,w,RevertToParent,CurrentTime);XWarpPointer(x,None,w,0,0,0,0,40,40);XSync(x,False);pump_mapper(c);
  auto key=[&](unsigned evdev,bool down){assert(XTestFakeKeyEvent(x,evdev+8,down,CurrentTime));XSync(x,False);pump_mapper(c);};
  // All functional keyboard control types cross a real X server and Wayland seat.
  for(int kind: {1,2,3,4,5,6}) {
    anvil_mapper_setup(kind);key(17,true);assert(anvil_mapper_contacts()==1);
    if(kind==1){key(32,true);assert(anvil_mapper_contacts()==1);key(32,false);}
    if(kind==2){anvil_mapper_advance(100);assert(!anvil_mapper_contacts());}
    if(kind==3){anvil_mapper_advance(100);assert(anvil_mapper_action()==1);anvil_mapper_advance(100);assert(anvil_mapper_action()==0);}
    if(kind==5){anvil_mapper_advance(10000);assert(!anvil_mapper_contacts());}
    if(kind==4||kind==6) {
      XWarpPointer(x,None,w,0,0,0,0,100+kind,70);XSync(x,False);pump_mapper(c);assert(anvil_mapper_action()==2);
      if(kind==6){XTestFakeButtonEvent(x,1,True,CurrentTime);XSync(x,False);pump_mapper(c);assert(anvil_mapper_contacts()==2);
        XTestFakeButtonEvent(x,1,False,CurrentTime);XSync(x,False);pump_mapper(c);assert(anvil_mapper_contacts()==1);}
    }
    key(17,false);assert(!anvil_mapper_contacts());
  }
  // A finite held macro cannot be restarted by repeated XKB DOWN events.
  anvil_mapper_setup(5);key(17,true);anvil_mapper_advance(10000);int packets=anvil_mapper_packets();
  XEvent repeat{};repeat.xkey.type=KeyPress;repeat.xkey.display=x;repeat.xkey.window=w;repeat.xkey.keycode=25;
  XSendEvent(x,w,False,KeyPressMask,&repeat);XSync(x,False);pump_mapper(c);
  assert(!anvil_mapper_contacts()&&anvil_mapper_packets()==packets);key(17,false);
  // Blur cancels contacts; refocus while W is held must not replay it.
  anvil_mapper_setup(3);key(17,true);XSetInputFocus(x,DefaultRootWindow(x),RevertToParent,CurrentTime);XSync(x,False);pump_mapper(c);assert(!anvil_mapper_contacts());
  XSetInputFocus(x,w,RevertToParent,CurrentTime);XSync(x,False);pump_mapper(c);
  XSendEvent(x,w,False,KeyPressMask,&repeat);XSync(x,False);pump_mapper(c);assert(!anvil_mapper_contacts());
  key(17,false);key(17,true);assert(anvil_mapper_contacts()==1);key(17,false);
  // Fresh unmapped input still takes the normal guest forwarding path.
  int forwarded=anvil_mapper_forwarded();key(44,true);key(44,false);assert(anvil_mapper_forwarded()==forwarded+2);
  // Check the actual X cursor pixels through the shared guest bridge and
  // native host, on this test's private X server. No Android session is used.
  anvil_mapper_cursor_bind(c.compositor,c.shm,a.surface,pointer,enter_serial,
    wl_proxy_marshal_array_constructor_versioned,wl_proxy_add_listener,
    wl_proxy_destroy,wl_proxy_get_version);
  auto cursor=[&](unsigned width,unsigned height,unsigned hot_x,unsigned hot_y,bool visible) {
    pump_mapper(c);XSync(x,False);
    auto *image=XFixesGetCursorImage(x);assert(image);
    assert(image->width==width&&image->height==height&&image->xhot==hot_x&&image->yhot==hot_y);
    unsigned ink=0;
    for(unsigned i=0;i<unsigned(image->width)*image->height;++i)ink+=(image->pixels[i]>>24)!=0;
    assert(visible?ink>0:ink==0);XFree(image);
  };
  cursor(26,32,3,2,true); // No guest request yet: production SHM arrow.
  anvil_mapper_cursor_request(nullptr,0,0);cursor(26,32,3,2,true);
  anvil_mapper_cursor_capture();cursor(1,1,0,0,false);
  anvil_mapper_cursor_release();cursor(26,32,3,2,true);
  auto *guest_cursor=wl_compositor_create_surface(c.compositor);
  anvil_mapper_cursor_request(guest_cursor,2,1);cursor(26,32,3,2,true);
  auto *cursor_buffer=buffer(c,8,8,0xff00ff00);
  wl_surface_attach(guest_cursor,cursor_buffer,0,0);wl_surface_commit(guest_cursor);
  anvil_mapper_cursor_frame(guest_cursor,1);cursor(8,8,2,1,true);
  anvil_mapper_cursor_capture();cursor(1,1,0,0,false);
  anvil_mapper_cursor_request(guest_cursor,2,1);cursor(1,1,0,0,false);
  anvil_mapper_cursor_release();cursor(8,8,2,1,true);
  wl_surface_attach(guest_cursor,nullptr,0,0);wl_surface_commit(guest_cursor);
  anvil_mapper_cursor_frame(guest_cursor,0);cursor(26,32,3,2,true);
  anvil_mapper_cursor_finish();wl_surface_destroy(guest_cursor);wl_buffer_destroy(cursor_buffer);
  wl_keyboard_release(keyboard);wl_pointer_release(pointer);xdg_toplevel_destroy(a.top);xdg_surface_destroy(a.xdg);wl_surface_destroy(a.surface);wl_buffer_destroy(content);
  pump_mapper(c);globals_destroy(c,registry_proxy);wl_display_disconnect(c.display);XCloseDisplay(x);
  puts("PASS private real-X11 seat -> shared guest bridge: controls, blur/repeat suppression, visible fallback/guest cursor pixels and capture hide/restore (fake Android sink)");return 0;
}
