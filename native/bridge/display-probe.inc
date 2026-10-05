/* Opt-in disposable experiment only. Exercise upstream display hotplug through
 * its registered callback, without private C++ layout offsets. Never included
 * in the production build. A callback return is NOT an SF acknowledgement. */
#ifdef ANVIL_DISPLAY_GROWTH_PROBE
extern int __system_property_get(const char *, char *);
struct display_probe_dl_info {
  const char *filename;
  void *base;
  const char *symbol;
  void *symbol_address;
};
extern int dladdr(const void *, struct display_probe_dl_info *);
static int display_probe_width, display_probe_height;
static struct object *display_probe_surface;
static int display_probe_property(const char *name) {
  char value[128] = {0};
  int number = 0;
  if (__system_property_get(name, value) > 0)
    sscanf(value, "%d", &number);
  return number;
}
static void display_probe_apply(struct object *top, struct proxy *p, void *states,
                                int w, int h) {
  if (w < 64 || h < 64 || w > 8192 || h > 8192) return;
  pthread_mutex_lock(mutex);
  int current_w = display_probe_property("waydroid.display_width");
  int current_h = display_probe_property("waydroid.display_height");
  int physical_w = logical_to_task(w), physical_h = logical_to_task(h);
  /* Refuse fixed dimensions and any shrink. This probe deliberately does not
   * override user properties, grow silently past the budget or retry forever. */
  int allowed = current_w > 0 && current_h > 0 &&
      physical_w >= current_w && physical_h >= current_h &&
      physical_w <= 8192 && physical_h <= 8192 &&
      (int64_t)physical_w * physical_h <= 16777216 &&
      !display_probe_property("persist.waydroid.width") &&
      !display_probe_property("persist.waydroid.height") &&
      w >= display_probe_width && h >= display_probe_height &&
      (w != display_probe_width || h != display_probe_height);
  if (allowed) {
    display_probe_width = w;
    display_probe_height = h;
    display_probe_surface = top->surface;
  }
  pthread_mutex_unlock(mutex);
  if (!allowed) return;
  struct display_probe_dl_info info = {0};
  if (dladdr((const void *)top->listener[0], &info))
    __android_log_print(4, "AnvilDroid", "display probe listener library=%s offset=0x%lx",
                        info.filename, (unsigned long)((uintptr_t)top->listener[0] - (uintptr_t)info.base));
  __android_log_print(4, "AnvilDroid", "display probe callback logical=%dx%d android=%dx%d old=%dx%d",
                      w, h, physical_w, physical_h, current_w, current_h);
  /* No bridge lock: upstream writes Wayland geometry through our interceptor. */
  ((void (*)(void *, struct proxy *, int32_t, int32_t, void *))top->listener[0])(
      top->listener_data, p, w, h, states);
  __android_log_print(4, "AnvilDroid", "display probe after callback properties=%dx%d (not SF acknowledgement)",
                      display_probe_property("waydroid.display_width"),
                      display_probe_property("waydroid.display_height"));
  pthread_mutex_lock(mutex);
  display_probe_surface = 0;
  pthread_mutex_unlock(mutex);
}
static void display_probe(struct object *top, struct proxy *p, void *states) {
  int w = 0, h = 0;
  char extra;
  void *request = fopen("/data/waydroid_tmp/anvildroid/display-probe", "r");
  if (!request) return;
  char line[128];
  int valid = fgets(line, sizeof(line), request) &&
      sscanf(line, "%d %d %c", &w, &h, &extra) == 2;
  fclose(request);
  if (valid) display_probe_apply(top, p, states, w, h);
}
#endif
