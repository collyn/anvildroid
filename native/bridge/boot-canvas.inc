/* Public property_get interposition. Stock HWC reads the logical
 * display size through libcutils during calibration. No system property is
 * written, and explicit size/padding settings always take precedence. */
#ifdef ANVIL_BOOT_CANVAS
#include "boot-canvas.h"
static int boot_hint_width, boot_hint_height, boot_width, boot_height, boot_decided;
typedef int (*property_get_fn)(const char *, char *, const char *);
static property_get_fn boot_real_property_get;
/* Reserved Android pixels are allocation capacity, not the maximized window's
 * geometry. Strict xdg-shell compositors require the configured geometry even
 * when its attached buffer is larger. App and full-UI surfaces own their size. */
static void boot_canvas_geometry(struct object *o, union argument *a) {
  struct object *s=o?o->surface:0;
  if(!s||s->app||s->full_ui||boot_width<=0||boot_height<=0)return;
  if(s->boot_geometry_w>0&&s->boot_geometry_h>0) {
    a[0].i=a[1].i=0;
    a[2].i=s->boot_geometry_w;a[3].i=s->boot_geometry_h;
  }
}
int property_get(const char *key, char *value, const char *fallback) {
  property_get_fn get = __atomic_load_n(&boot_real_property_get, __ATOMIC_ACQUIRE);
  if (!get) {
    void *library = dlopen("libcutils.so", 2);
    get = library ? (property_get_fn)dlsym(library, "property_get") : 0;
    if (!get) abort();
    __atomic_store_n(&boot_real_property_get, get, __ATOMIC_RELEASE);
  }
  if (strcmp(key, "persist.waydroid.width") && strcmp(key, "persist.waydroid.height"))
    return get(key, value, fallback);
  /* Desktop bridges reserve a larger canvas for independent app windows.
   * A stock headless compositor instead requires its exact configured size. */
  const char *runtime = getenv("ANVILDROID_RUNTIME_ID");
  if (!runtime || !*runtime) return get(key, value, fallback);
  char existing[92];
  const char *explicit_keys[] = {"persist.waydroid.width", "persist.waydroid.height",
      "persist.waydroid.width_padding", "persist.waydroid.height_padding"};
  for (unsigned i = 0; i < sizeof(explicit_keys)/sizeof(explicit_keys[0]); ++i)
    if (get(explicit_keys[i], existing, 0) > 0)
      return get(key, value, fallback);
  pthread_mutex_lock(mutex);
  if (!boot_decided && boot_hint_width > 1 && boot_hint_height > 1) {
    boot_decided = 1;
    char canvas[64];
    int cap_w = 0, cap_h = 0;
    if (get("persist.anvildroid.canvas", canvas, 0) > 0 &&
        sscanf(canvas, "%dx%d", &cap_w, &cap_h) == 2 && cap_w > 0 && cap_h > 0 &&
        boot_canvas_capacity(cap_w, cap_h, hwc_scale_120, &boot_width, &boot_height))
      __android_log_print(4, "AnvilDroid", "boot canvas capacity=%dx%d scale=%d/120 hint=%dx%d",
                          cap_w, cap_h, hwc_scale_120, boot_hint_width, boot_hint_height);
    else if (boot_canvas_size(boot_hint_width, boot_hint_height, hwc_scale_120,
                              &boot_width, &boot_height))
      __android_log_print(4, "AnvilDroid", "boot canvas logical=%dx%d scale=%d/120 hint=%dx%d",
                          boot_width, boot_height, hwc_scale_120, boot_hint_width, boot_hint_height);
    else
      __android_log_print(5, "AnvilDroid", "boot canvas reserve refused: invalid hint or allocation budget");
  }
  int result = !strcmp(key, "persist.waydroid.width") ? boot_width : boot_height;
  pthread_mutex_unlock(mutex);
  if (result > 0) return snprintf(value, 92, "%d", result);
  return get(key, value, fallback);
}
#endif
