/* Included after bridge's ABI helpers. All Wayland work holds mutex and runs
 * only from Wayland/render callbacks. The socket worker never uses proxies. */
#include "../ime/text-state.h"
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned);
extern int getsockopt(int, int, int, void *, unsigned *);
extern int setsockopt(int, int, int, const void *, unsigned);
extern long recv(int, void *, size_t, int);
extern long ime_socket_send(int, const void *, size_t, int) __asm__("send");
extern int close(int);
struct ime_pollfd { int fd; short events, revents; };
extern int poll(struct ime_pollfd *, unsigned long, int);

static struct proxy *ime_manager, *ime_seat, *ime_text, *ime_surface;
static struct wl_display *ime_display;
static uint32_t ime_manager_name, ime_serial, ime_first_serial;
static uint64_t ime_generation, ime_enabled_generation;
static int ime_connected, ime_active, ime_type, ime_enabled, ime_broken;
static char ime_package[256];
static struct anvil_ime_state ime_state;
/* Android may acknowledge a batch after the editor has already changed its
 * composing state. Keep the authenticated channel alive and absorb bursts. */
static char ime_queue[64][32769];
static unsigned ime_head, ime_tail;

static const struct interface ime_text_interface;
static const void *ime_object_types[2];
static const void *ime_surface_types[1];
static const struct message ime_manager_requests[] = {
    {"destroy", "", 0}, {"get_text_input", "no", ime_object_types}};
static const struct interface ime_manager_interface = {
    "zwp_text_input_manager_v3", 1, 2, ime_manager_requests, 0, 0};
static const struct message ime_requests[] = {
    {"destroy", "", 0}, {"enable", "", 0}, {"disable", "", 0},
    {"set_surrounding_text", "sii", 0}, {"set_text_change_cause", "u", 0},
    {"set_content_type", "uu", 0}, {"set_cursor_rectangle", "iiii", 0},
    {"commit", "", 0}};
static const struct message ime_events[] = {
    {"enter", "o", ime_surface_types}, {"leave", "o", ime_surface_types},
    {"preedit_string", "?sii", 0}, {"commit_string", "?s", 0},
    {"delete_surrounding_text", "uu", 0}, {"done", "u", 0}};
static const struct interface ime_text_interface = {
    "zwp_text_input_v3", 1, 8, ime_requests, 6, ime_events};

static void ime_enqueue(const char *value) {
  if (!ime_connected || ime_broken) return;
  unsigned next = (ime_tail + 1) % 64;
  if (next == ime_head) {
    __android_log_print(5, "AnvilDroidIme", "text batch queue full; reconnecting");
    ime_broken = 1;
    return;
  }
  snprintf(ime_queue[ime_tail], sizeof(ime_queue[0]), "%s", value);
  ime_tail = next;
}
static char *ime_quote(char *out, const char *text) {
  static const char hex[] = "0123456789abcdef";
  *out++ = '"';
  for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
    if (*p == '"' || *p == '\\') { *out++ = '\\'; *out++ = (char)*p; }
    else if (*p < 32) {
      *out++ = '\\'; *out++ = 'u'; *out++ = '0'; *out++ = '0';
      *out++ = hex[*p >> 4]; *out++ = hex[*p & 15];
    } else *out++ = (char)*p;
  }
  *out++ = '"';
  return out;
}
static void ime_batch(const struct anvil_ime_batch *batch) {
  /* Two 4096-byte strings need up to 49152 bytes when escaped. */
  char json[50000];
  int n = snprintf(json, sizeof(json),
      "{\"v\":1,\"op\":\"batch\",\"generation\":%llu,\"deleteBefore\":%u,"
      "\"deleteAfter\":%u,\"cursorBegin\":%d,\"cursorEnd\":%d,\"commit\":",
      (unsigned long long)ime_enabled_generation, batch->delete_before_bytes,
      batch->delete_after_bytes, batch->cursor_begin, batch->cursor_end);
  char *end = ime_quote(json + n, batch->commit);
  memcpy(end, ",\"preedit\":", 11);
  end = ime_quote(end + 11, batch->preedit);
  *end++ = '}'; *end = 0;
  if ((size_t)(end - json) > 32768) { ime_broken = 1; return; }
  ime_enqueue(json);
}
static void ime_commit_state(void) {
  wayland_send(ime_text, 7, 0);
  ++ime_serial;
}
static void ime_sync(void) {
  if (!ime_text) return;
  struct object *s = find(ime_surface);
  int klass = ime_type & 15, variation = ime_type & 0xff0;
  int password = (klass == 1 && (variation == 0x80 || variation == 0x90 ||
                                 variation == 0xe0)) ||
                 (klass == 2 && variation == 0x10);
  int want = ime_connected && !ime_broken && ime_active && !password &&
             s && s->app && !s->overlay && s->app_id && !strcmp(s->app_id, ime_package);
  if (ime_enabled && (!want || ime_enabled_generation != ime_generation)) {
    wayland_send(ime_text, 2, 0);
    ime_commit_state();
    ime_enabled = 0;
    anvil_ime_focus(&ime_state, 0);
    ime_head = ime_tail = 0;
    __android_log_print(4, "AnvilDroidIme", "text-input disabled");
  }
  if (want && !ime_enabled) {
    ime_enabled = 1;
    ime_enabled_generation = ime_generation;
    anvil_ime_focus(&ime_state, 1);
    wayland_send(ime_text, 1, 0);
    union argument content[2] = {{.u = 0}, {.u = klass == 2 ? 3 : 0}};
    wayland_send(ime_text, 5, content);
    /* No fake surrounding text. Cursor rectangle awaits Android cursor data. */
    ime_commit_state();
    ime_first_serial = ime_serial;
    __android_log_print(4, "AnvilDroidIme", "text-input enabled for %s generation %llu",
                        ime_package, (unsigned long long)ime_generation);
  }
}
static void ime_enter(void *data, struct proxy *text, struct proxy *surface) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  ime_surface = surface;
  ime_sync();
  pthread_mutex_unlock(mutex);
}
static void ime_leave(void *data, struct proxy *text, struct proxy *surface) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  if (ime_surface == surface) ime_surface = 0;
  ime_sync();
  pthread_mutex_unlock(mutex);
}
static void ime_preedit(void *data, struct proxy *text, const char *value, int32_t a, int32_t b) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  anvil_ime_preedit(&ime_state, value, a, b);
  pthread_mutex_unlock(mutex);
}
static void ime_insert(void *data, struct proxy *text, const char *value) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  anvil_ime_commit(&ime_state, value);
  pthread_mutex_unlock(mutex);
}
static void ime_delete_event(void *data, struct proxy *text, uint32_t a, uint32_t b) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  anvil_ime_delete(&ime_state, a, b);
  pthread_mutex_unlock(mutex);
}
static void ime_done(void *data, struct proxy *text, uint32_t serial) {
  (void)data; (void)text;
  pthread_mutex_lock(mutex);
  struct anvil_ime_batch batch;
  struct object *focused=find(ime_surface);
  if (anvil_ime_done(&ime_state, serial, ime_serial, &batch) && ime_enabled &&
      !(focused&&focused->overlay) && ime_generation == ime_enabled_generation && (int32_t)(serial - ime_first_serial) >= 0) {
    ime_batch(&batch);
    __android_log_print(4, "AnvilDroidIme", "text batch queued (no content logged)");
  }
  pthread_mutex_unlock(mutex);
}
static void (*ime_listener[])(void) = {
    (void (*)(void))ime_enter, (void (*)(void))ime_leave,
    (void (*)(void))ime_preedit, (void (*)(void))ime_insert,
    (void (*)(void))ime_delete_event, (void (*)(void))ime_done};
static void ime_create(void) {
  if (!ime_manager || !ime_seat || ime_text) return;
  union argument a[2] = {{.o = 0}, {.o = ime_seat}};
  ime_text = real_marshal(ime_manager, 1, a, &ime_text_interface, 1);
  if (ime_text) {
    real_listen(ime_text, ime_listener, 0);
    __android_log_print(4, "AnvilDroidIme", "text-input-v3 bound");
  }
}
static void ime_global(void *data, struct proxy *registry, uint32_t name,
                       const char *interface, uint32_t version) {
  struct object *o = data;
  if (!strcmp(interface, "zxdg_decoration_manager_v1")) {
    pthread_mutex_lock(mutex);
    struct decoration_binding *binding=decoration_for(o->display);
    if (!binding && version >= 1) {
      union argument a[4] = {{.u = name}, {.s = interface}, {.u = 1}, {.o = 0}};
      struct proxy *manager = real_marshal(registry, 0, a, &decoration_manager_interface, 1);
      /* EGL may advertise globals on a temporary registry queue. Bridge-owned
       * bindings outlive that queue; their children use HWC's default dispatch. */
      if (manager) {
        if (real_set_queue) real_set_queue(manager, 0);
        binding=calloc(1,sizeof(*binding));
        if (!binding) abort();
        binding->display=o->display;binding->manager=manager;binding->name=name;
        binding->next=decoration_bindings;decoration_bindings=binding;
        decoration_manager=manager;decoration_global_name=name;decoration_available=1;
        __android_log_print(4,"AnvilDroid","decoration binding display=%p",o->display);
      }
    }
    pthread_mutex_unlock(mutex);
    /* One owner: do not let upstream create a second decoration per toplevel. */
    return;
  }
  if (!strcmp(interface, "zwp_text_input_manager_v3")) {
    pthread_mutex_lock(mutex);
    if (!ime_manager) {
      ime_manager_name = name;
      union argument a[4] = {{.u = name}, {.s = interface}, {.u = 1}, {.o = 0}};
      ime_manager = real_marshal(registry, 0, a, &ime_manager_interface, 1);
      ime_display = o->display;
      if (ime_manager && real_set_queue) real_set_queue(ime_manager, 0);
      ime_create();
    }
    pthread_mutex_unlock(mutex);
  }
  ((void (*)(void *, struct proxy *, uint32_t, const char *, uint32_t))o->listener[0])(
      o->listener_data, registry, name, interface, version);
}
static void ime_global_remove(void *data, struct proxy *registry, uint32_t name) {
  struct object *o = data;
  struct decoration_binding *binding=decoration_for(o->display);
  if (binding && name == binding->name) {
    pthread_mutex_lock(mutex);
    /* Manager destruction leaves existing decorations alive. Release the old
     * binding so a later advertised global can serve newly created windows. */
    wayland_send(binding->manager, 0, 0);
    real_destroy(binding->manager);
    struct decoration_binding **at=&decoration_bindings;
    while (*at!=binding) at=&(*at)->next;
    *at=binding->next;free(binding);
    decoration_manager=decoration_bindings?decoration_bindings->manager:0;
    decoration_global_name=decoration_bindings?decoration_bindings->name:0;
    decoration_available=decoration_manager!=0;
    pthread_mutex_unlock(mutex);
    return;
  }
  if (name == ime_manager_name) {
    pthread_mutex_lock(mutex);
    ime_broken = 1;
    ime_sync();
    pthread_mutex_unlock(mutex);
  }
  ((void (*)(void *, struct proxy *, uint32_t))o->listener[1])(o->listener_data, registry, name);
}
static void (*ime_registry_listener[])(void) = {
    (void (*)(void))ime_global, (void (*)(void))ime_global_remove};

#include "ime-transport.inc"
