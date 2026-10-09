/* CSD for the fixed Android canvas. Only the small frame uses SHM;
 * Android buffers stay on their existing GPU/viewport path. */
#include "csd-layout.h"
#include "keymap-font.h"
extern long syscall(long, ...);
extern int ftruncate(int, long);
extern void *mmap(void *, size_t, int, int, int, long);
extern int munmap(void *, size_t);
static struct wl_proxy *csd_compositor, *csd_subcompositor, *csd_shm;
static const struct wl_interface *csd_surface_i, *csd_subsurface_i, *csd_pool_i, *csd_buffer_i;
static struct { struct wl_proxy *surface, *subsurface; int width,height; } csd_pieces[4];
struct csd_buffer { struct wl_proxy *proxy; struct csd_buffer *next; };
static struct csd_buffer *csd_buffers;
static uint32_t decoration_pending, decoration_mode;
static int maximized, csd_part=-1, csd_x, csd_y, csd_pressed, csd_suppress;
static void *top_data;
static int csd_active(void) { return top && (!decoration || decoration_mode==1); }
static int csd_visible(void) { return csd_active() && !fullscreen; }
static void csd_track(struct wl_proxy *p,const struct wl_interface *i) {
  if(!strcmp(i->name,"wl_compositor")){csd_compositor=p;csd_surface_i=i->methods[0].types[0];}
  if(!strcmp(i->name,"wl_subcompositor")){csd_subcompositor=p;csd_subsurface_i=i->methods[1].types[0];}
  if(!strcmp(i->name,"wl_shm")){csd_shm=p;csd_pool_i=i->methods[0].types[0];csd_buffer_i=csd_pool_i->methods[0].types[0];}
}
static void csd_release(void *data,struct wl_proxy *p) {
  struct csd_buffer **at=&csd_buffers;
  while(*at && *at!=data)at=&(*at)->next;
  if(*at){struct csd_buffer *old=*at;*at=old->next;marshal(p,0,NULL,1,1,NULL);free(old);}
}
static void (*csd_buffer_listener[])(void)={(void(*)(void))csd_release};
static struct wl_proxy *csd_paint(int part, int w, int h, int maximized) {
  if (w < 1 || h < 1 || w > 8204 || h > 8204) return 0;
  /* memfd_create on the bridge's supported x86_64 Android ABI. */
  int fd = (int)syscall(319, "anvildroid-csd", 1);
  if (fd < 0) return 0;
  size_t size = (size_t)w * h * 4;
  if (ftruncate(fd, (long)size)) { close(fd); return 0; }
  uint32_t *pixels = mmap(0, size, 3, 1, fd, 0);
  if (pixels == (void *)-1) { close(fd); return 0; }
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
    uint32_t color = 0xff30343b;
    if (part == 0 && y >= CSD_BORDER) {
      int action = csd_hit(part, x, y, w, h);
      int cx = w - CSD_BORDER - CSD_BUTTON / 2;
      if (action == CSD_MAXIMIZE) cx -= CSD_BUTTON;
      if (action == CSD_MINIMIZE) cx -= 2 * CSD_BUTTON;
      int dx = x - cx, dy = y - (CSD_BORDER + (CSD_HEADER-CSD_BORDER)/2);
      int ink = (action == CSD_CLOSE && dx >= -5 && dx <= 5 && (dy == dx || dy == -dx)) ||
        (action == CSD_MINIMIZE && dx >= -5 && dx <= 5 && dy == 3) ||
        (action == CSD_MAXIMIZE && dx >= -5 && dx <= 5 && dy >= -5 && dy <= 5 &&
          (dx == -5 || dx == 5 || dy == -5 || dy == 5 || (maximized && dy == -2)));
      if (ink) color = 0xfff2f2f2;
    }
    pixels[(size_t)y*w+x] = color;
  }
  if(part==0 && w>260)keymap_text(pixels,w,h,16,15,"ANDROID",0xfff2f2f2);

  union wl_argument pool_a[3] = {{.o=0}, {.i=fd}, {.i=(int)size}};
  struct wl_proxy *pool = marshal(csd_shm, 0, csd_pool_i, 1, 0, pool_a);
  struct wl_proxy *buffer = 0;
  if (pool) {
    union wl_argument a[6] = {{.o=0},{.i=0},{.i=w},{.i=h},{.i=w*4},{.u=0}};
    buffer = marshal(pool, 0, csd_buffer_i, 1, 0, a);
    marshal(pool, 1, NULL, 1, 1, NULL);
  }
  munmap(pixels, size);
  close(fd);
  if (buffer) {
    struct csd_buffer *b = calloc(1, sizeof(*b));
    if (!b) { marshal(buffer,0,NULL,1,1,NULL); return NULL; }
    b->proxy = buffer; b->next = csd_buffers; csd_buffers = b;
    listen(buffer, csd_buffer_listener, b);
  }
  return buffer;
}
static void csd_geometry(union wl_argument *g) {
  int visible=csd_visible();
  g[0].i=visible?-CSD_BORDER:0;g[1].i=visible?-CSD_HEADER:0;
  g[2].i=host_w+(visible?2*CSD_BORDER:0);
  g[3].i=host_h+(visible?CSD_HEADER+CSD_BORDER:0);
}
static void csd_sync(void) {
  if(!csd_compositor || !csd_subcompositor || !csd_shm)return;
  int widths[4]={host_w+2*CSD_BORDER,CSD_BORDER,CSD_BORDER,host_w};
  int heights[4]={CSD_HEADER,host_h+CSD_BORDER,host_h+CSD_BORDER,CSD_BORDER};
  int xs[4]={-CSD_BORDER,-CSD_BORDER,host_w,0};
  int ys[4]={-CSD_HEADER,0,0,host_h};
  for(int i=0;i<4;i++) {
    if(!csd_pieces[i].surface && csd_visible()) {
      union wl_argument id={.o=NULL};
      csd_pieces[i].surface=marshal(csd_compositor,0,csd_surface_i,1,0,&id);
      if(!csd_pieces[i].surface)continue;
      union wl_argument a[3]={{.o=NULL},{.o=csd_pieces[i].surface},{.o=root_surface}};
      csd_pieces[i].subsurface=marshal(csd_subcompositor,1,csd_subsurface_i,1,0,a);
      if(!csd_pieces[i].subsurface){marshal(csd_pieces[i].surface,0,NULL,1,1,NULL);csd_pieces[i].surface=NULL;continue;}
    }
    if(!csd_pieces[i].surface)continue;
    union wl_argument pos[2]={{.i=xs[i]},{.i=ys[i]}};
    marshal(csd_pieces[i].subsurface,1,NULL,1,0,pos);
    if(!csd_visible()) {
      union wl_argument empty[3]={{.o=NULL},{.i=0},{.i=0}};
      marshal(csd_pieces[i].surface,1,NULL,1,0,empty);
      csd_pieces[i].width=csd_pieces[i].height=0;
    } else if(csd_pieces[i].width!=widths[i] || csd_pieces[i].height!=heights[i]) {
      struct wl_proxy *buffer=csd_paint(i,widths[i],heights[i],maximized);
      if(!buffer)continue;
      union wl_argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
      marshal(csd_pieces[i].surface,1,NULL,1,0,attach);
      union wl_argument damage[4]={{.i=0},{.i=0},{.i=widths[i]},{.i=heights[i]}};
      marshal(csd_pieces[i].surface,2,NULL,1,0,damage);
      csd_pieces[i].width=widths[i];csd_pieces[i].height=heights[i];
    }
    marshal(csd_pieces[i].surface,6,NULL,1,0,NULL);
  }
}
static int csd_surface_part(struct wl_proxy *s) {
  for(int i=0;i<4;i++)if(s && s==csd_pieces[i].surface)return i;
  return -1;
}
static void csd_destroy(void) {
  for(int i=0;i<4;i++) {
    if(csd_pieces[i].subsurface)marshal(csd_pieces[i].subsurface,0,NULL,1,1,NULL);
    if(csd_pieces[i].surface)marshal(csd_pieces[i].surface,0,NULL,1,1,NULL);
    csd_pieces[i].surface=csd_pieces[i].subsurface=NULL;
    csd_pieces[i].width=csd_pieces[i].height=0;
  }
  decoration_mode=decoration_pending=0;csd_part=-1;csd_pressed=0;csd_suppress=0;maximized=0;
}
static void csd_button(uint32_t serial,uint32_t button,uint32_t state) {
  if(csd_part<0 || !top || button!=0x110)return;
  int action=csd_hit(csd_part,csd_x,csd_y,csd_pieces[csd_part].width,csd_pieces[csd_part].height);
  if(state) {
    csd_pressed=action;
    if(ime_seat && (action==CSD_MOVE || (action>0 && action<16 && !maximized))) {
      union wl_argument a[3]={{.o=ime_seat},{.u=serial},{.u=(uint32_t)action}};
      marshal(top,action==CSD_MOVE?5:6,NULL,get_version(top),0,a);
    }
  } else {
    int activate=action && action==csd_pressed;csd_pressed=0;
    if(!activate)return;
    if(action==CSD_MINIMIZE)marshal(top,13,NULL,get_version(top),0,NULL);
    if(action==CSD_MAXIMIZE)marshal(top,maximized?10:9,NULL,get_version(top),0,NULL);
    if(action==CSD_CLOSE && top_original[1])((void(*)(void*,struct wl_proxy*))top_original[1])(top_data,top);
  }
}
