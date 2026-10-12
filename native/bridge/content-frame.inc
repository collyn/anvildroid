/* The pinned NVIDIA HWC translates displayFrame into window-local coordinates.
 * Observe its exported method, matching window* only against listener_data.
 * Never inspect C++ object layouts. Stock HWC does not call this method.
 * Keep the original call outside the bridge lock: it sends Wayland requests. */
#define CONTENT_FRAME_METHOD "_ZN6window19apply_content_frameEiiiid"
typedef void (*content_frame_fn)(void *, int, int, int, int, double);
static content_frame_fn content_frame_original;

void anvil_content_frame(void *, int, int, int, int, double)
  __asm__(CONTENT_FRAME_METHOD);
void anvil_content_frame(void *context, int x, int y, int w, int h, double scale) {
  pthread_mutex_lock(mutex);
  if (!content_frame_original) {
    void *hwc=dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so",2|4);
    content_frame_original=hwc?(content_frame_fn)dlsym(hwc,CONTENT_FRAME_METHOD):0;
  }
  content_frame_fn original=content_frame_original;
  if (context && w>0 && h>0 && x>=0 && y>=0 && x<=8192 && y<=8192 &&
      scale>=1 && scale<=16) {
    for (struct object *top=objects;top;top=top->next) {
      if (top->surface && top->listener_data==context &&
          !strcmp(top->p->interface->name,"xdg_toplevel")) {
        struct object *s=top->surface;
        s->frame_origin_x=x;s->frame_origin_y=y;s->frame_origin_valid=1;
        break;
      }
    }
  }
  pthread_mutex_unlock(mutex);
  if (!original || original==anvil_content_frame) abort();
  original(context,x,y,w,h,scale);
}
