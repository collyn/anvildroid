#include "bridge.c"
#include <assert.h>
extern int socketpair(int, int, int, int *);
int __android_log_print(int level, const char *tag, const char *format, ...) {
  (void)level; (void)tag; (void)format; return 0;
}
static int enables, disables;
static struct proxy *record_ime(struct proxy *p, uint32_t op, union argument *a,
                               const struct interface *type, uint32_t version) {
  (void)p; (void)a; (void)type; (void)version;
  if (op == 1) ++enables;
  if (op == 2) ++disables;
  return 0;
}
static uint32_t version_ime(struct proxy *p) { (void)p; return 1; }
static int shm_presentation, globals;
int __system_property_get(const char *name, char *value) {
  if (shm_presentation && !strcmp(name, "waydroid.anvil_shm_presentation")) {
    snprintf(value, 92, "true"); return 4;
  }
  value[0] = 0; return 0;
}
static void registry_global(void *data, struct proxy *registry, uint32_t name,
                            const char *interface, uint32_t version) {
  (void)data; (void)registry; (void)name; (void)interface; (void)version; ++globals;
}
int main(void) {
  void (*listeners[])(void) = {(void (*)(void))registry_global};
  struct object registry = {.listener = listeners};
  ime_global(&registry, 0, 1, "zwp_linux_dmabuf_v1", 3);
  assert(globals == 1);
  shm_presentation = 1;
  ime_global(&registry, 0, 1, "zwp_linux_dmabuf_v1", 3);
  assert(globals == 1);
  ime_global(&registry, 0, 2, "wl_shm", 1);
  assert(globals == 2);
  shm_presentation = 0;
  int sockets[2];
  assert(!socketpair(1, 1, 0, sockets));
  char hello[] = "{}", frame[6];
  assert(ime_write(sockets[0], hello));
  assert(ime_transfer(sockets[1], frame, sizeof(frame), 0));
  assert(frame[0] == 0 && frame[1] == 0 && frame[2] == 0 && frame[3] == 2);
  assert(frame[4] == '{' && frame[5] == '}');
  close(sockets[0]); close(sockets[1]);
  struct proxy text = {&ime_text_interface}, surface = {0};
  struct object object = {.p = &surface, .app = 1, .app_id = "org.test"};
  objects = &object;
  real_marshal = record_ime; get_version = version_ime;
  ime_text = &text; ime_surface = &surface;
  ime_connected = ime_active = 1; ime_type = 1; ime_generation = 5;
  snprintf(ime_package, sizeof(ime_package), "org.other");
  ime_sync();
  assert(!ime_enabled && !enables);
  snprintf(ime_package, sizeof(ime_package), "org.test");
  ime_sync();
  assert(ime_enabled && enables == 1);
  ime_insert(0, &text, "Tôi \"x\"\n");
  ime_preedit(0, &text, "ế", 3, 3);
  ime_done(0, &text, ime_serial);
  assert(ime_tail == 1);
  assert(strstr(ime_queue[0], "\"commit\":\"Tôi \\\"x\\\"\\u000a\""));
  assert(strstr(ime_queue[0], ",\"preedit\":\"ế\"}"));
  assert(strstr(ime_queue[0], "\"cursorBegin\":1"));
  ++ime_generation;
  ime_sync();
  assert(enables == 2 && disables == 1 && ime_head == ime_tail);
  ime_insert(0, &text, "stale");
  ime_done(0, &text, ime_first_serial - 1);
  assert(ime_head == ime_tail);
  ime_type = 0x81; /* Password does not opt in to host input yet. */
  ime_sync();
  assert(!ime_enabled);
  ime_type = 1;
  ime_sync();
  ime_leave(0, &text, &surface);
  assert(!ime_enabled && !ime_state.active);
  ime_enter(0, &text, &surface);
  assert(ime_enabled);
  ime_connected = 0;
  ime_sync();
  assert(!ime_enabled);
  ime_connected=1;ime_sync();assert(ime_enabled);
  struct keymap_overlay edit={0};object.overlay=&edit;
  ime_insert(0,&text,"must not reach game while editing");
  unsigned queued=ime_tail;ime_done(0,&text,ime_serial);assert(ime_tail==queued);
  ime_sync();assert(!ime_enabled&&ime_head==ime_tail);
  object.overlay=0;ime_sync();assert(ime_enabled);
  /* CursorAnchorInfo is Android display-space; host fitting must remove the
   * task/frame origin before positioning candidates, then apply its viewport. */
  scale_has_viewporter=1;object.viewport=&object;
  object.fit_x=100;object.fit_y=200;object.fit_sw=400;object.fit_sh=200;
  object.fit_w=800;object.fit_h=600;object.frame_origin_x=20;object.frame_origin_y=30;
  int x=120,y=230,h=10;
  ime_cursor_geometry(&object,&x,&y,&h);
  assert(x==0&&y==100&&h==20);
}
