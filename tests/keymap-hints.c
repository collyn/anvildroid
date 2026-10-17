#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int l, const char *t, const char *f, ...) {
  (void)l;
  (void)t;
  (void)f;
  return 0;
}
static const struct interface si = {.name = "wl_surface"},
                              ri = {.name = "wl_region"},
                              subi = {.name = "wl_subsurface"},
                              ci = {.name = "wl_compositor"},
                              sci = {.name = "wl_subcompositor"},
                              shi = {.name = "wl_shm"},
                              pi = {.name = "wl_shm_pool"},
                              bi = {.name = "wl_buffer"};
static struct proxy app = {&si}, hint = {&si}, region = {&ri}, sub = {&subi},
                    compositor = {&ci}, subcompositor = {&sci}, shm = {&shi},
                    pool = {&pi}, buffer = {&bi};
static int region_add, input_empty, attached, fail_region, keys, buttons,
    motions, leaves;
static int requests;
static void *release_data;
static struct proxy *marshal(struct proxy *p, uint32_t op, union argument *a,
                             const struct interface *i, uint32_t v) {
  (void)i;
  (void)v;
  ++requests;
  if (p == &compositor)
    return op == 0 ? &hint : fail_region ? 0 : &region;
  if (p == &region && op == 1)
    ++region_add;
  if (p == &hint && op == 5) {
    assert(a[0].o == &region);
    input_empty = 1;
  }
  if (p == &hint && op == 1 && a[0].o) {
    assert(input_empty && !region_add);
    ++attached;
  }
  if (p == &subcompositor)
    return &sub;
  if (p == &shm)
    return &pool;
  if (p == &pool && op == 0)
    return &buffer;
  return 0;
}
static void destroy(struct proxy *p) { (void)p; }
static uint32_t version(struct proxy *p) {
  (void)p;
  return 1;
}
static int listen_buffer(struct proxy *p, void (**listener)(void), void *d) {
  assert(p == &buffer && listener == csd_buffer_listener);
  release_data = d;
  return 0;
}
static void key(void *d, struct proxy *p, uint32_t s, uint32_t t, uint32_t k,
                uint32_t st) {
  (void)d;
  (void)p;
  (void)s;
  (void)t;
  (void)k;
  (void)st;
  ++keys;
}
static void leave(void *d, struct proxy *p, uint32_t s, struct proxy *surface) {
  (void)d;
  (void)p;
  (void)s;
  (void)surface;
  ++leaves;
}
static void enter(void *d, struct proxy *p, uint32_t s, struct proxy *surface,
                  int32_t x, int32_t y) {
  (void)d;
  (void)p;
  (void)s;
  (void)surface;
  (void)x;
  (void)y;
}
static void motion(void *d, struct proxy *p, uint32_t t, int32_t x, int32_t y) {
  (void)d;
  (void)p;
  (void)t;
  (void)x;
  (void)y;
  ++motions;
}
static void button(void *d, struct proxy *p, uint32_t s, uint32_t t, uint32_t b,
                   uint32_t st) {
  (void)d;
  (void)p;
  (void)s;
  (void)t;
  (void)b;
  (void)st;
  ++buttons;
}
int main(void) {
  real_marshal = marshal;
  real_destroy = destroy;
  real_listen = listen_buffer;
  get_version = version;
  csd_compositor = &compositor;
  csd_subcompositor = &subcompositor;
  csd_shm = &shm;
  csd_surface_i = &si;
  csd_region_i = &ri;
  csd_subsurface_i = &subi;
  csd_pool_i = &pi;
  csd_buffer_i = &bi;
  struct object s = {
      .p = &app, .app = 1, .app_id = "com.test.game", .has_bounds = 1};
  objects = &s;
  task_count = 1;
  tasks[0].id = 7;
  snprintf(tasks[0].package, sizeof(tasks[0].package), "com.test.game");
  struct keymap_model m = {.selected = -1};
  assert(keymap_add(&m, 150, 90, 400, 240));
  assert(keymap_assign(&m, 30));
  keymap_hints_set(&s, &m, 7);
  assert(s.hints && s.hints->passive && !s.overlay);
  fail_region = 1;
  keymap_overlay_sync(&s, 0, 0, 400, 240);
  assert(!attached && !s.hints->piece.surface);
  keymap_hints_destroy(&s);
  fail_region = 0;
  keymap_hints_set(&s, &m, 7);
  keymap_overlay_sync(&s, 0, 0, 400, 240);
  assert(attached == 1 && input_empty && !region_add && !s.overlay);
  int settled=requests;
  for(int i=0;i<300;++i)keymap_overlay_sync(&s,0,0,400,240);
  assert(requests==settled);
  csd_release(release_data, &buffer);
  assert(!keymap_overlay_enter(&s, &hint));
  void (*kc[])(void) = {0, 0, (void (*)(void))leave, (void (*)(void))key};
  struct object keyboard = {.keyboard_focus = &s, .listener = kc};
  keyboard.next=objects;objects=&keyboard;
  keymap_key(&keyboard, 0, 1, 1, 30, 1);
  /* Mapped keys are consumed even when the injection backend is unavailable. */
  assert(keys == 0 && (s.hints->active_keys[0] & (1u << 30)));
  keymap_key(&keyboard, 0, 1, 1, 30, 0);
  assert(keys == 0 && !s.hints->active_keys[0]);
  assert(s.hints->flash_ticks[30] == 4);
  uint32_t *flash = calloc(400 * 240, sizeof(*flash));
  assert(flash);
  keymap_hints_paint(flash, 400, 240, s.hints);
  uint32_t lit = flash[90 * 400 + 177];
  for(int i=0;i<3;++i)keymap_hints_poll("r-test");
  keymap_hints_paint(flash, 400, 240, s.hints);
  assert(flash[90 * 400 + 177] == lit);
  keymap_hints_poll("r-test");
  keymap_hints_paint(flash, 400, 240, s.hints);
  assert(!s.hints->flash_ticks[30] && flash[90 * 400 + 177] != lit);
  keymap_key(&keyboard, 0, 1, 1, 30, 1);
  for(int i=0;i<4;++i)keymap_hints_poll("r-test");
  keymap_hints_paint(flash, 400, 240, s.hints);
  assert(flash[90 * 400 + 177] == lit);
  keymap_key(&keyboard, 0, 1, 1, 30, 0);
  free(flash);
  keymap_key(&keyboard, 0, 1, 1, 31, 1);
  assert(keys == 1 && !s.hints->active_keys[0]);
  keymap_key(&keyboard, 0, 1, 1, 30, 1);
  keymap_kleave(&keyboard, 0, 2, &app);
  assert(leaves == 1 && keyboard.keyboard_leave_pending);
  keymap_dispatch_finished(0,0);
  assert(leaves == 1 && !s.hints->active_keys[0] && !keyboard.keyboard_focus);
  void (*pc[])(void) = {(void (*)(void))enter, (void (*)(void))leave,
                        (void (*)(void))motion, (void (*)(void))button};
  struct object pointer = {.listener = pc};
  csd_pointer_enter(&pointer, 0, 1, &app, 100, 200);
  csd_pointer_motion(&pointer, 0, 2, 300, 400);
  csd_pointer_button(&pointer, 0, 3, 3, 0x110, 1);
  csd_pointer_button(&pointer, 0, 4, 4, 0x110, 0);
  assert(motions == 1 && buttons == 2 && !pointer.csd_suppress);
  uint32_t *pixels = calloc(400 * 240, sizeof(*pixels));
  assert(pixels);
  keymap_hints_paint(pixels, 400, 240, s.hints);
  assert(pixels[0] == 0);
  uint32_t idle = pixels[90 * 400 + 177];
  s.hints->active_keys[0] = 1u << 30;
  keymap_hints_paint(pixels, 400, 240, s.hints);
  assert(pixels[90 * 400 + 177] != idle);
  for (int i = 0; i < 400 * 240; ++i) {
    unsigned a = pixels[i] >> 24;
    assert(((pixels[i] >> 16) & 255) <= a);
    assert(((pixels[i] >> 8) & 255) <= a);
    assert((pixels[i] & 255) <= a);
  }
  free(pixels);
  /* Draft/cancel never mutates saved hints; deleting all saved points removes
   * them, and a recycled/ambiguous task cannot inherit an old surface's hints.
   */
  struct keymap_overlay *edit = calloc(1, sizeof(*edit));
  assert(edit);
  edit->model = m;
  s.overlay = edit;
  s.overlay->model.points[0].key = 31;
  keymap_overlay_destroy(&s);
  assert(s.hints->model.points[0].key == 30);
  tasks[0].id = 8;
  keymap_overlay_sync(&s, 0, 0, 400, 240);
  assert(!s.hints && !s.hints_probe);
  keymap_hints_set(&s, &m, 8);
  m.count = 0;
  keymap_hints_set(&s, &m, 8);
  keymap_overlay_sync(&s, 0, 0, 400, 240);
  assert(!s.hints);
  assert(!csd_buffers);
}
