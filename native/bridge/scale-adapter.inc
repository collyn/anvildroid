/* Mirror the supported HWC's scale callbacks, not the desktop setting:
 * HWC retains max(integer(current), output_scale) on output changes and uses
 * preferred_scale/120 during calibration when viewporter is bound. Once the
 * compositor's fractional preferred_scale has been observed, it drives the
 * mirror both ways: the integer output scale (KWin advertises ceil(fractional),
 * e.g. 2 at 125%) must no longer poison it, or windows map at 1/4 size and
 * never recover when the desktop returns to 100%. */
static int hwc_scale_120 = 120, scale_has_viewporter, scale_fractional_120;
static int task_to_logical(int value, int ceil_result) {
  int64_t n = (int64_t)value * 120;
  int64_t q = n / hwc_scale_120, rem = n % hwc_scale_120;
  if (ceil_result && rem > 0) ++q;
  if (!ceil_result && rem < 0) --q;
  return (int)q;
}
static int logical_to_task(int value) {
  return (int)(((int64_t)value * hwc_scale_120 + 119) / 120);
}
static void scale_output_geometry(void *data, struct proxy *p, int32_t x,
    int32_t y, int32_t pw, int32_t ph, int32_t subpixel,
    const char *make, const char *model, int32_t transform) {
  struct object *o=data;
  ((void (*)(void *,struct proxy *,int32_t,int32_t,int32_t,int32_t,int32_t,
             const char *,const char *,int32_t))o->listener[0])(
    o->listener_data,p,x,y,pw,ph,subpixel,make,model,transform);
}
static void scale_output_mode(void *data, struct proxy *p, uint32_t flags,
    int32_t w, int32_t h, int32_t refresh) {
  struct object *o=data;
  ((void (*)(void *,struct proxy *,uint32_t,int32_t,int32_t,int32_t))o->listener[1])(
    o->listener_data,p,flags,w,h,refresh);
}
static void scale_output_done(void *data, struct proxy *p) {
  struct object *o=data;
  ((void (*)(void *,struct proxy *))o->listener[2])(o->listener_data,p);
}
static void scale_output_scale(void *data, struct proxy *p, int32_t scale) {
  struct object *o=data;
  pthread_mutex_lock(mutex);
  if (!scale_fractional_120) {
    int current = hwc_scale_120 / 120;
    if (scale > 0 && scale <= 16)
      hwc_scale_120 = (current > scale ? current : scale) * 120;
  }
  pthread_mutex_unlock(mutex);
  ((void (*)(void *,struct proxy *,int32_t))o->listener[3])(o->listener_data,p,scale);
}
static void scale_fractional(void *data, struct proxy *p, uint32_t scale) {
  struct object *o=data;
  pthread_mutex_lock(mutex);
  if (scale_has_viewporter && scale >= 120 && scale <= 1920) {
    scale_fractional_120 = (int)scale;
    hwc_scale_120 = (int)scale;
  }
  pthread_mutex_unlock(mutex);
  ((void (*)(void *,struct proxy *,uint32_t))o->listener[0])(o->listener_data,p,scale);
}
static void (*scale_output_listener[])(void) = {
  (void (*)(void))scale_output_geometry, (void (*)(void))scale_output_mode,
  (void (*)(void))scale_output_done, (void (*)(void))scale_output_scale};
static void (*scale_fractional_listener[])(void) = {(void (*)(void))scale_fractional};
