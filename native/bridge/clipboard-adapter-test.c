#include "bridge.c"
#include <assert.h>
int __android_log_print(int level, const char *tag, const char *format, ...) {
  (void)level;
  (void)tag;
  (void)format;
  return 0;
}
extern int socketpair(int, int, int, int *);
static int display_fd;
static struct proxy manager_proxy={&clip_manager_interface},device_proxy={&clip_device_interface};
static int destroyed;
static struct proxy *clip_test_marshal(struct proxy *p,uint32_t op,union argument *a,
                                      const struct interface *type,uint32_t version) {
  (void)p;(void)op;(void)a;(void)version;
  return type==&clip_manager_interface?&manager_proxy:type==&clip_device_interface?&device_proxy:0;
}
static uint32_t clip_test_version(struct proxy *p){(void)p;return 1;}
static int clip_test_listen(struct proxy *p,void (**listener)(void),void *data) {
  (void)p;(void)listener;(void)data;return 0;
}
static void clip_test_destroy(struct proxy *p){(void)p;++destroyed;}
static int display_test_fd(struct wl_display *display) {
  assert(display == (struct wl_display *)1);
  return display_fd;
}
int main(void) {
  int pair[2], before = 0, after = 0;
  unsigned option_size = sizeof(before);
  assert(!socketpair(1, 1, 0, pair));
  display_fd = pair[0];
  assert(!getsockopt(pair[0], 1, 7, &before, &option_size));
  real_display_get_fd = display_test_fd;
  display_send_capacity((struct wl_display *)1);
  option_size = sizeof(after);
  assert(!getsockopt(pair[0], 1, 7, &after, &option_size) && after >= before);
  display_fd = -1;
  display_send_capacity((struct wl_display *)1);
  real_display_get_fd = 0;
  close(pair[0]);
  close(pair[1]);
  /* Compare the hidden-symbol fallback ABI to libwayland's real core protocol. */
  void *core = dlopen("libwayland-client.so.0", 2);
  assert(core);
  const struct interface *fallbacks[] = {
      &clip_manager_interface, &clip_source_interface, &clip_offer_interface,
      &clip_device_interface};
  const char *symbols[] = {"wl_data_device_manager_interface", "wl_data_source_interface",
                          "wl_data_offer_interface", "wl_data_device_interface"};
  for (unsigned i = 0; i < 4; ++i) {
    const struct interface *expected = dlsym(core, symbols[i]);
    assert(expected && !strcmp(expected->name, fallbacks[i]->name));
    for (int event = 0; event < 2; ++event) {
      int count = event ? fallbacks[i]->event_count : fallbacks[i]->method_count;
      const struct message *actual = event ? fallbacks[i]->events : fallbacks[i]->methods;
      const struct message *standard = event ? expected->events : expected->methods;
      for (int j = 0; j < count; ++j) {
        assert(!strcmp(actual[j].name, standard[j].name));
        assert(!strcmp(actual[j].signature, standard[j].signature));
      }
    }
  }
  const unsigned char original[] = {0, 1, 2, 127, 128, 255};
  char encoded[16];
  unsigned char decoded[16];
  assert(anvil_b64_encode(encoded, original, sizeof(original)) == 8);
  assert(anvil_b64_decode(decoded, sizeof(decoded), encoded, 8) == 6 &&
         !memcmp(decoded, original, 6));
  assert(anvil_b64_decode(decoded, sizeof(decoded), "AA=A", 4) < 0);
  assert(anvil_b64_decode(decoded, sizeof(decoded), "AB==", 4) < 0);
  assert(anvil_b64_decode(decoded, 1, "AAAA", 4) < 0);
  assert(anvil_b64_decode(decoded, sizeof(decoded), "Pz8_", 4) == 3 &&
         !memcmp(decoded, "???", 3));
  assert(anvil_b64_decode(decoded, sizeof(decoded), "77-_", 4) == 3 &&
         !memcmp(decoded, "\xef\xbf\xbf", 3));
  assert(clipboard_receive(
             "{\"op\":\"clipboard_begin\",\"id\":1,\"bytes\":1048577}") < 0);
  assert(clipboard_receive(
             "{\"op\":\"clipboard_begin\",\"id\":1,\"bytes\":1048576}") == 1);
  unsigned char *bytes = calloc(CLIP_CAP, 1);
  for (size_t i = 0; i < CLIP_CAP; ++i)
    bytes[i] = (unsigned char)('a' + i % 26);
  char value[32769];
  for (size_t offset = 0; offset < CLIP_CAP;) {
    size_t n = CLIP_CAP - offset;
    if (n > 18000)
      n = 18000;
    int used = snprintf(
        value, sizeof(value),
        "{\"op\":\"clipboard_chunk\",\"id\":1,\"offset\":%u,\"data\":\"",
        (unsigned)offset);
    size_t length = anvil_b64_encode(value + used, bytes + offset, n);
    memcpy(value + used + length, "\"}", 3);
    assert(clipboard_receive(value) == 1);
    offset += n;
  }
  assert(clipboard_receive("{\"op\":\"clipboard_end\",\"id\":2}") < 0);
  assert(clipboard_receive("{\"op\":\"clipboard_end\",\"id\":1}") == 1);
  assert(clip_dirty && clip_size == CLIP_CAP &&
         !memcmp(clip_current, bytes, CLIP_CAP));
  clip_pending = bytes;
  clip_pending_size = CLIP_CAP;
  clip_outgoing = 1;
  size_t received = 0;
  int frames = 0;
  while (clipboard_next(value, sizeof(value))) {
    assert(strlen(value) <= 32768);
    ++frames;
    if (strstr(value, "clipboard_chunk")) {
      char *p = strstr(value, "\"data\":\"") + 8;
      char *end = strchr(p, '"');
      unsigned char chunk[18000];
      int n = anvil_b64_decode(chunk, sizeof(chunk), p, (size_t)(end - p));
      assert(n > 0 && !memcmp(chunk, clip_current + received, (size_t)n));
      received += (size_t)n;
    }
  }
  assert(received == CLIP_CAP && frames == 61);
  assert(
      clipboard_receive(
          "{\"op\":\"clipboard_begin\",\"id\":3,\"bytes\":0,\"clear\":true}") ==
      1);
  assert(clipboard_receive("{\"op\":\"clipboard_end\",\"id\":3}") == 1);
  assert(clip_clear && !clip_size);
  free(clip_current);
  clip_current = 0;
  /* Binding and offer ownership work without an exported get_user_data. */
  real_marshal=clip_test_marshal;real_listen=clip_test_listen;
  get_version=clip_test_version;real_destroy=clip_test_destroy;
  clip_manager_i=&clip_manager_interface;clip_device_i=&clip_device_interface;clip_source_i=&clip_source_interface;
  struct object registry={0};struct proxy seat={0},offer={&clip_offer_interface};
  ime_seat=&seat;
  ime_global(&registry,0,5,"wl_data_device_manager",3);
  assert(clip_manager==&manager_proxy&&clip_device==&device_proxy);
  clip_device_offer(0,clip_device,&offer);
  assert(clip_offers&&clip_offers->p==&offer);
  clip_device_selection(0,clip_device,&offer);
  assert(clip_offer==clip_offers);
  clip_device_selection(0,clip_device,&offer);
  assert(!destroyed&&clip_offer==clip_offers); /* Same offer is not freed. */
  clipboard_disconnect();assert(!clip_offers&&!clip_offer&&destroyed==3);
  return 0;
}
