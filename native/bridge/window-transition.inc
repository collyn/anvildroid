/* Optional HWC ABI, invoked only by the task worker outside the Wayland
 * mutex. The HWC owns window lifetime; the bridge supplies explicit intent. */
typedef int (*hwc_transition_fn)(int, int, const char *);
static hwc_transition_fn hwc_transition_api;
static unsigned hwc_transition_probe;
static int hwc_transition_probed;
static int hwc_transition_call(int operation, int task, const char *package) {
  if (!hwc_transition_probed && !(hwc_transition_probe++ % 20)) {
    void *hwc=dlopen("/vendor/lib64/hw/hwcomposer.waydroid.so",2|4);
    if (hwc) {
      hwc_transition_api=(hwc_transition_fn)dlsym(hwc,"anvil_hwc_window_transition_v1");
      hwc_transition_probed=1;
    }
  }
  return hwc_transition_api ? hwc_transition_api(operation,task,package) : 0;
}
