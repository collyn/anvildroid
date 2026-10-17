#define ANVIL_BOOT_CANVAS 1
#include "../native/bridge/bridge.c"
#include <assert.h>
extern int setenv(const char *, const char *, int);
extern int unsetenv(const char *);
static const char *explicit_key;
static const char *canvas_prop;
static void configured(void *data,struct proxy *p,int32_t w,int32_t h,void *states) {
  (void)data;(void)p;(void)states;assert(w>0&&h>0);
}
int __android_log_print(int level, const char *tag, const char *format, ...) {
  (void)level; (void)tag; (void)format; return 0;
}
static int get(const char *key, char *value, const char *fallback) {
  if (explicit_key && !strcmp(explicit_key, key)) return snprintf(value, 92, "1000");
  if (canvas_prop && !strcmp(key, "persist.anvildroid.canvas"))
    return snprintf(value, 92, "%s", canvas_prop);
  return snprintf(value, 92, "%s", fallback ? fallback : "");
}
int main(void) {
  int w, h;
  assert(boot_canvas_size(1920, 1080, 120, &w, &h));
  assert(w == 3904 && h == 2288);
  assert(boot_canvas_size(1536, 864, 240, &w, &h));
  assert(w == 1568 && h == 928);
  assert(boot_canvas_size(1536, 864, 150, &w, &h));
  assert((int64_t)w * 150 / 120 >= 3136);
  assert((int64_t)h * 150 / 120 >= 1856);
  assert(!boot_canvas_size(7680, 4320, 120, &w, &h));
  assert(!boot_canvas_size(1920, 1080, 0, &w, &h));
  boot_real_property_get = get;
  char value[92];
  unsetenv("ANVILDROID_RUNTIME_ID");
  boot_hint_width=800; boot_hint_height=568; canvas_prop="1920x1036";
  property_get("persist.waydroid.width", value, "800");
  assert(!strcmp(value, "800") && !boot_decided);
  setenv("ANVILDROID_RUNTIME_ID", "", 1);
  property_get("persist.waydroid.height", value, "568");
  assert(!strcmp(value, "568") && !boot_decided);
  setenv("ANVILDROID_RUNTIME_ID", "default", 1);
  boot_hint_width=boot_hint_height=0; canvas_prop=0;
  assert(property_get("persist.waydroid.width", value, 0) == 0);
  assert(!boot_decided);
  boot_hint_width=1920; boot_hint_height=1080;
  explicit_key="persist.waydroid.height";
  assert(!property_get("persist.waydroid.width", value, 0));
  assert(!boot_decided); /* Never mix an automatic width with explicit height. */
  explicit_key=0;
  property_get("persist.waydroid.width", value, 0);
  assert(!strcmp(value, "3904"));
  boot_hint_width=800; hwc_scale_120=240;
  property_get("persist.waydroid.height", value, 0);
  assert(!strcmp(value, "2288")); /* Stable boot pair, not per-app configures. */
  explicit_key="persist.waydroid.width";
  property_get("persist.waydroid.width", value, 0);
  assert(!strcmp(value, "1000"));
  property_get("unrelated", value, "keep");
  assert(!strcmp(value, "keep"));
  explicit_key=0;
  /* Worker-probed physical workarea capacity, margins + rounding in one place. */
  assert(boot_canvas_capacity(1920, 1036, 120, &w, &h));
  assert(w == 1984 && h == 1280);
  assert(boot_canvas_capacity(1536, 864, 240, &w, &h));
  assert(w == 800 && h == 640);
  assert(!boot_canvas_capacity(8192, 8192, 120, &w, &h));
  assert(!boot_canvas_capacity(32, 32, 120, &w, &h));
  assert(!boot_canvas_capacity(1920, 1036, 0, &w, &h));
  /* Capacity property wins over the 2x hint reserve. */
  boot_decided=0; boot_width=boot_height=0;
  boot_hint_width=1920; boot_hint_height=1080; hwc_scale_120=120;
  canvas_prop="1920x1036";
  property_get("persist.waydroid.width", value, 0);
  assert(!strcmp(value, "1984"));
  property_get("persist.waydroid.height", value, 0);
  assert(!strcmp(value, "1280"));
  boot_hint_width=800; canvas_prop=0; /* Stable boot pair, not per-app configures. */
  property_get("persist.waydroid.width", value, 0);
  assert(!strcmp(value, "1984"));
  /* Invalid capacity falls back to the 2x reserve. */
  for (const char **invalid = (const char*[]){
       "garbage", "0x500", "20000x100", "", "1x2", 0}; *invalid; ++invalid) {
    boot_decided=0; boot_width=boot_height=0;
    boot_hint_width=1920; boot_hint_height=1080; hwc_scale_120=120;
    canvas_prop=*invalid;
    property_get("persist.waydroid.width", value, 0);
    assert(!strcmp(value, "3904"));
    property_get("persist.waydroid.height", value, 0);
    assert(!strcmp(value, "2288"));
  }
  canvas_prop=0;
  /* Capacity converts to logical at higher HWC scales. */
  boot_decided=0; boot_width=boot_height=0;
  boot_hint_width=1920; boot_hint_height=1080; hwc_scale_120=240;
  canvas_prop="1920x1036";
  property_get("persist.waydroid.width", value, 0);
  assert(!strcmp(value, "992"));
  property_get("persist.waydroid.height", value, 0);
  assert(!strcmp(value, "640"));
  /* Explicit user size still wins over the capacity property. */
  boot_decided=0; boot_width=boot_height=0;
  boot_hint_width=1920; boot_hint_height=1080; hwc_scale_120=120;
  canvas_prop="1920x1036"; explicit_key="persist.waydroid.height";
  assert(!property_get("persist.waydroid.width", value, 0));
  assert(!boot_decided);
  explicit_key=0; canvas_prop=0;
  /* Wayland maximized geometry stays exact while Android's reserve survives. */
  struct object canvas={0},top={.surface=&canvas};
  void (*listener[])(void)={(void (*)(void))configured};top.listener=listener;
  uint32_t maximized=1;
  struct {size_t size,alloc;uint32_t *data;} states={sizeof(maximized),sizeof(maximized),&maximized};
  boot_width=1984;boot_height=1280;
  configure(&top,0,1600,968,&states);
  union argument geometry[4]={{.i=0},{.i=0},{.i=1984},{.i=1280}};
  struct object xdg={.surface=&canvas};boot_canvas_geometry(&xdg,geometry);
  assert(geometry[2].i==1600&&geometry[3].i==968);
  assert(boot_width==1984&&boot_height==1280); /* No capacity regression. */
  canvas.app=1;geometry[2].i=1200;geometry[3].i=900;
  boot_canvas_geometry(&xdg,geometry);assert(geometry[2].i==1200&&geometry[3].i==900);
  canvas.app=0;canvas.full_ui=1;
  boot_canvas_geometry(&xdg,geometry);assert(geometry[2].i==1200&&geometry[3].i==900);
  canvas.full_ui=0;states.size=0;configure(&top,0,1600,968,&states);
  boot_canvas_geometry(&xdg,geometry);assert(geometry[2].i==1200&&geometry[3].i==900);
}
