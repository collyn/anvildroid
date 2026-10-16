/* Plain UTF-8 clipboard through wl_data_device and the authenticated IME.
 * Wayland callbacks own proxies; the socket worker only handles bytes/FDs.
 * Android text is bounded to 1 MiB and chunked, never logged. */
#define ANVIL_CLIPBOARD_ADAPTER 1
#include "../ime/base64.h"
extern int pipe2(int *, int);
extern int *__errno(void);
extern int *__errno_location(void);
static void *(*clip_user_data)(struct proxy *);
extern int fcntl(int, int, ...);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int memcmp(const void *, const void *, size_t);
static void keymap_wake(void);
#define CLIP_CAP (1024u * 1024u)
/* Some Waydroid composers export the Wayland functions but hide the core
 * interface data symbols. Keep v1 wire metadata just as for text-input-v3;
 * never fall back to HWC's blocking clipboard reader in that case. */
static const struct interface clip_offer_interface, clip_source_interface,
    clip_device_interface;
static const void *clip_new_source_types[] = {&clip_source_interface};
static const void *clip_new_device_types[] = {&clip_device_interface, 0};
static const void *clip_new_offer_types[] = {&clip_offer_interface};
static const void *clip_enter_types[] = {0, 0, 0, 0, &clip_offer_interface};
static const void *clip_drag_types[] = {&clip_source_interface, 0, 0, 0};
static const void *clip_selection_types[] = {&clip_source_interface, 0};
static const struct message clip_manager_requests[] = {
    {"create_data_source", "n", clip_new_source_types},
    {"get_data_device", "no", clip_new_device_types}};
static const struct interface clip_manager_interface = {
    "wl_data_device_manager", 1, 2, clip_manager_requests, 0, 0};
static const struct message clip_source_requests[] = {
    {"offer", "s", 0}, {"destroy", "", 0}};
static const struct message clip_source_events[] = {
    {"target", "?s", 0}, {"send", "sh", 0}, {"cancelled", "", 0}};
static const struct interface clip_source_interface = {
    "wl_data_source", 1, 2, clip_source_requests, 3, clip_source_events};
static const struct message clip_offer_requests[] = {
    {"accept", "u?s", 0}, {"receive", "sh", 0}, {"destroy", "", 0}};
static const struct message clip_offer_events[] = {{"offer", "s", 0}};
static const struct interface clip_offer_interface = {
    "wl_data_offer", 1, 3, clip_offer_requests, 1, clip_offer_events};
static const struct message clip_device_requests[] = {
    {"start_drag", "?oo?ou", clip_drag_types},
    {"set_selection", "?ou", clip_selection_types}};
static const struct message clip_device_events[] = {
    {"data_offer", "n", clip_new_offer_types},
    {"enter", "uoff?o", clip_enter_types}, {"leave", "", 0},
    {"motion", "uff", 0}, {"drop", "", 0},
    {"selection", "?o", clip_new_offer_types}};
static const struct interface clip_device_interface = {
    "wl_data_device", 1, 2, clip_device_requests, 6, clip_device_events};
struct clip_source {
  struct proxy *p;
  unsigned char *bytes;
  size_t size;
  struct clip_source *next;
};
struct clip_offer {
  struct proxy *p;
  int mime;
};
struct clip_io {
  int fd, sending;
  unsigned char *bytes;
  size_t size, offset;
  unsigned ticks;
  struct clip_io *next;
};
static const struct interface *clip_manager_i, *clip_source_i, *clip_device_i;
static struct proxy *clip_manager, *clip_device;
static struct clip_source *clip_sources;
static struct clip_offer *clip_offer;
static struct clip_io *clip_ios;
static uint32_t clip_serial, clip_manager_name;
static unsigned char *clip_current, *clip_pending, *clip_receiving,
    *clip_transmitting;
static size_t clip_size, clip_pending_size, clip_expected, clip_received,
    clip_tx_size, clip_tx_offset;
static unsigned long long clip_receive_id, clip_send_id;
static int clip_dirty, clip_clear, clip_pending_clear, clip_receiving_clear,
    clip_tx_clear, clip_tx_stage;
static int clip_outgoing, clip_initial;
static int clip_equal(const unsigned char *bytes, size_t size, int clear) {
  return clip_current && clip_size == size && clip_clear == clear &&
         !memcmp(clip_current, bytes, size);
}
static void clip_remember(const unsigned char *bytes, size_t size, int clear) {
  free(clip_current);
  clip_current = calloc(size + 1, 1);
  if (clip_current)
    memcpy(clip_current, bytes, size);
  clip_size = size;
  clip_clear = clear;
}
static void clipboard_input(uint32_t serial) { clip_serial = serial; }
static struct clip_io *clip_io_new(int fd, int sending,
                                   const unsigned char *bytes, size_t size) {
  unsigned count = 0;
  for (struct clip_io *at = clip_ios; at; at = at->next)
    ++count;
  if (count >= 8) {
    close(fd);
    return 0;
  }
  struct clip_io *io = calloc(1, sizeof(*io));
  if (!io) {
    close(fd);
    return 0;
  }
  io->bytes = calloc(sending ? size + 1 : CLIP_CAP + 1, 1);
  if (!io->bytes) {
    free(io);
    close(fd);
    return 0;
  }
  if (sending)
    memcpy(io->bytes, bytes, size);
  io->fd = fd;
  io->sending = sending;
  io->size = size;
  fcntl(fd, 4, fcntl(fd, 3, 0) | 0x800);
  io->next = clip_ios;
  clip_ios = io;
  return io;
}
static void clip_source_target(void *data, struct proxy *p, const char *mime) {
  (void)data;
  (void)p;
  (void)mime;
}
static void clip_source_send(void *data, struct proxy *p, const char *mime,
                             int32_t fd) {
  (void)p;
  struct clip_source *s = data;
  pthread_mutex_lock(mutex);
  if (!strcmp(mime, "text/plain;charset=utf-8") || !strcmp(mime, "text/plain"))
    clip_io_new(fd, 1, s->bytes, s->size);
  else
    close(fd);
  pthread_mutex_unlock(mutex);
}
static void clip_source_cancel(void *data, struct proxy *p) {
  pthread_mutex_lock(mutex);
  struct clip_source *s = data;
  struct clip_source **at = &clip_sources;
  while (*at && *at != s)
    at = &(*at)->next;
  if (*at)
    *at = s->next;
  wayland_send(p, 1, 0);
  real_destroy(p);
  free(s->bytes);
  free(s);
  pthread_mutex_unlock(mutex);
}
static void (*clip_source_listener[])(void) = {
    (void (*)(void))clip_source_target, (void (*)(void))clip_source_send,
    (void (*)(void))clip_source_cancel};
static void clip_offer_mime(void *data, struct proxy *p, const char *mime) {
  (void)p;
  struct clip_offer *o = data;
  if (!strcmp(mime, "text/plain;charset=utf-8"))
    o->mime = 2;
  else if (!o->mime && !strcmp(mime, "text/plain"))
    o->mime = 1;
}
static void (*clip_offer_listener[])(void) = {(void (*)(void))clip_offer_mime};
static void clip_device_offer(void *data, struct proxy *p,
                              struct proxy *offer) {
  (void)data;
  (void)p;
  struct clip_offer *o = calloc(1, sizeof(*o));
  if (!o) {
    wayland_send(offer, 2, 0);
    real_destroy(offer);
    return;
  }
  o->p = offer;
  real_listen(offer, clip_offer_listener, o);
}
static void clip_device_selection(void *data, struct proxy *p,
                                  struct proxy *offer) {
  (void)data;
  (void)p;
  pthread_mutex_lock(mutex);
  struct clip_io **io_at = &clip_ios;
  while (*io_at) {
    struct clip_io *io = *io_at;
    if (!io->sending) {
      *io_at = io->next;
      close(io->fd);
      free(io->bytes);
      free(io);
    } else
      io_at = &io->next;
  }
  if (!offer && ime_surface && !clip_equal((const unsigned char *)"", 0, 1)) {
    clip_remember((const unsigned char *)"", 0, 1);
    free(clip_pending);
    clip_pending = calloc(1, 1);
    clip_pending_size = 0;
    clip_pending_clear = 1;
    clip_outgoing = clip_pending != 0;
  }
  if (clip_offer) {
    wayland_send(clip_offer->p, 2, 0);
    real_destroy(clip_offer->p);
    free(clip_offer);
    clip_offer = 0;
  }
  if (offer) {
    /* Listener data is ours even though bridge does not track this proxy. */
    struct clip_offer *o = clip_user_data ? clip_user_data(offer) : 0;
    clip_offer = o;
    if (o && o->mime) {
      int fds[2];
      if (!pipe2(fds, 0x80000 | 0x800)) {
        if (clip_io_new(fds[0], 0, 0, 0)) {
          union argument a[2] = {
              {.s = o->mime == 2 ? "text/plain;charset=utf-8" : "text/plain"},
              {.i = fds[1]}};
          wayland_send(offer, 1, a);
        }
        close(fds[1]);
      }
    }
  }
  pthread_mutex_unlock(mutex);
}
static void clip_drag_enter(void *d, struct proxy *p, uint32_t s,
                            struct proxy *w, int32_t x, int32_t y,
                            struct proxy *o) {
  (void)d;
  (void)p;
  (void)s;
  (void)w;
  (void)x;
  (void)y;
  (void)o;
}
static void clip_drag_leave(void *d, struct proxy *p) {
  (void)d;
  (void)p;
}
static void clip_drag_motion(void *d, struct proxy *p, uint32_t t, int32_t x,
                             int32_t y) {
  (void)d;
  (void)p;
  (void)t;
  (void)x;
  (void)y;
}
static void (*clip_device_listener[])(void) = {
    (void (*)(void))clip_device_offer, (void (*)(void))clip_drag_enter,
    (void (*)(void))clip_drag_leave,   (void (*)(void))clip_drag_motion,
    (void (*)(void))clip_drag_leave,   (void (*)(void))clip_device_selection};
static void clipboard_create(void) {
  if (!clip_manager || !ime_seat || clip_device || !clip_device_i)
    return;
  union argument a[2] = {{.o = 0}, {.o = ime_seat}};
  clip_device = real_marshal(clip_manager, 1, a, clip_device_i, 1);
  if (clip_device) {
    if (real_set_queue)
      real_set_queue(clip_device, 0);
    real_listen(clip_device, clip_device_listener, 0);
    __android_log_print(4,"AnvilDroidClipboard","asynchronous clipboard bridge bound");
  }
}
static void clipboard_sync(void) {
  if (!clip_dirty || !clip_device || !clip_serial || !ime_surface)
    return;
  struct clip_source *source = 0;
  if (!clip_clear) {
    source = calloc(1, sizeof(*source));
    if (!source)
      return;
    source->bytes = calloc(clip_size + 1, 1);
    if (!source->bytes) {
      free(source);
      return;
    }
    memcpy(source->bytes, clip_current, clip_size);
    source->size = clip_size;
    union argument id = {.o = 0};
    source->p = real_marshal(clip_manager, 0, &id, clip_source_i, 1);
    if (!source->p) {
      free(source->bytes);
      free(source);
      return;
    }
    if (real_set_queue)
      real_set_queue(source->p, 0);
    real_listen(source->p, clip_source_listener, source);
    union argument mime = {.s = "text/plain;charset=utf-8"};
    wayland_send(source->p, 0, &mime);
    mime.s = "text/plain";
    wayland_send(source->p, 0, &mime);
    source->next = clip_sources;
    clip_sources = source;
  }
  union argument a[2] = {{.o = source ? source->p : 0}, {.u = clip_serial}};
  wayland_send(clip_device, 1, a);
  clip_dirty = 0;
}
/* Called under mutex from socket worker. */
static int clipboard_receive(const char *value) {
  if (!strstr(value, "\"op\":\"clipboard_"))
    return 0;
  char *p = strstr(value, "\"id\":");
  unsigned long long id;
  if (!p || sscanf(p + 5, "%llu", &id) != 1)
    return -1;
  if (strstr(value, "\"op\":\"clipboard_begin\"")) {
    unsigned size;
    p = strstr(value, "\"bytes\":");
    if (!p || sscanf(p + 8, "%u", &size) != 1 || size > CLIP_CAP)
      return -1;
    free(clip_receiving);
    clip_receiving = calloc(size + 1, 1);
    if (!clip_receiving)
      return -1;
    clip_initial = strstr(value, "\"initial\":true") != 0;
    clip_expected = size;
    clip_received = 0;
    clip_receive_id = id;
    clip_receiving_clear = strstr(value, "\"clear\":true") != 0;
    if (clip_receiving_clear && size)
      return -1;
  } else if (strstr(value, "\"op\":\"clipboard_chunk\"")) {
    unsigned offset;
    p = strstr(value, "\"offset\":");
    if (!clip_receiving || id != clip_receive_id || !p ||
        sscanf(p + 9, "%u", &offset) != 1 || offset != clip_received)
      return -1;
    p = strstr(value, "\"data\":\"");
    if (!p)
      return -1;
    p += 8;
    size_t n = 0;
    while (p[n] && p[n] != '"')
      ++n;
    int decoded = anvil_b64_decode(clip_receiving + clip_received,
                                   clip_expected - clip_received, p, n);
    if (decoded < 0)
      return -1;
    clip_received += (size_t)decoded;
  } else if (strstr(value, "\"op\":\"clipboard_end\"")) {
    if (!clip_receiving || id != clip_receive_id ||
        clip_expected != clip_received)
      return -1;
    if (!clip_equal(clip_receiving, clip_received, clip_receiving_clear)) {
      if (!clip_initial) {
        clip_remember(clip_receiving, clip_received, clip_receiving_clear);
        clip_dirty = 1;
      } else if (clip_current) {
        free(clip_pending);
        clip_pending = calloc(clip_size + 1, 1);
        if (clip_pending) {
          memcpy(clip_pending, clip_current, clip_size);
          clip_pending_size = clip_size;
          clip_pending_clear = clip_clear;
          clip_outgoing = 1;
        }
      } else
        clip_remember(clip_receiving, clip_received, clip_receiving_clear);
    }
    free(clip_receiving);
    clip_receiving = 0;
  } else
    return -1;
  return 1;
}
static void clipboard_io_tick(void) {
  struct clip_io **at = &clip_ios;
  while (*at) {
    struct clip_io *io = *at;
    int done = 0, failed = 0;
    if (++io->ticks > 200)
      failed = 1;
    for (int burst = 0; !failed && burst < 16; ++burst) {
      long n =
          io->sending
              ? write(io->fd, io->bytes + io->offset, io->size - io->offset)
              : read(io->fd, io->bytes + io->size, CLIP_CAP + 1 - io->size);
      if (n < 0) {
        int error =
#ifdef __ANDROID__
            *__errno();
#else
            *__errno_location();
#endif
        if (error != 11 && error != 4)
          failed = 1;
        break;
      }
      if (io->sending) {
        io->offset += (size_t)n;
        if (io->offset == io->size) {
          done = 1;
          break;
        }
      } else {
        if (!n) {
          done = 1;
          break;
        }
        io->size += (size_t)n;
        if (io->size > CLIP_CAP) {
          failed = 1;
          break;
        }
      }
    }
    if (done && !io->sending && !clip_equal(io->bytes, io->size, 0)) {
      clip_remember(io->bytes, io->size, 0);
      free(clip_pending);
      clip_pending = io->bytes;
      io->bytes = 0;
      clip_pending_size = io->size;
      clip_pending_clear = 0;
      clip_outgoing = 1;
    }
    if (done || failed) {
      *at = io->next;
      close(io->fd);
      free(io->bytes);
      free(io);
    } else
      at = &io->next;
  }
}
/* Produce at most one chunk per socket iteration; receive ACK/state between
 * chunks. */
static int clipboard_next(char *value, size_t capacity) {
  clipboard_io_tick();
  if (!clip_tx_stage && clip_outgoing) {
    clip_transmitting = clip_pending;
    clip_pending = 0;
    clip_tx_size = clip_pending_size;
    clip_tx_clear = clip_pending_clear;
    clip_tx_offset = 0;
    clip_outgoing = 0;
    clip_tx_stage = 1;
    ++clip_send_id;
  }
  if (clip_tx_stage == 1) {
    snprintf(value, capacity,
             "{\"v\":1,\"op\":\"clipboard_begin\",\"id\":%llu,\"bytes\":%u,"
             "\"clear\":%s}",
             clip_send_id, (unsigned)clip_tx_size,
             clip_tx_clear ? "true" : "false");
    clip_tx_stage = 2;
    return 1;
  }
  if (clip_tx_stage == 2 && clip_tx_offset < clip_tx_size) {
    size_t n = clip_tx_size - clip_tx_offset;
    if (n > 18000)
      n = 18000;
    int used = snprintf(value, capacity,
                        "{\"v\":1,\"op\":\"clipboard_chunk\",\"id\":%llu,"
                        "\"offset\":%u,\"data\":\"",
                        clip_send_id, (unsigned)clip_tx_offset);
    size_t encoded =
        anvil_b64_encode(value + used, clip_transmitting + clip_tx_offset, n);
    memcpy(value + used + encoded, "\"}", 3);
    clip_tx_offset += n;
    return 1;
  }
  if (clip_tx_stage == 2) {
    snprintf(value, capacity, "{\"v\":1,\"op\":\"clipboard_end\",\"id\":%llu}",
             clip_send_id);
    free(clip_transmitting);
    clip_transmitting = 0;
    clip_tx_stage = 0;
    return 1;
  }
  return 0;
}
static void clipboard_disconnect(void) {
  if (clip_offer) {
    wayland_send(clip_offer->p, 2, 0);
    real_destroy(clip_offer->p);
    free(clip_offer);
    clip_offer = 0;
  }
  while (clip_sources) {
    struct clip_source *s = clip_sources;
    clip_sources = s->next;
    wayland_send(s->p, 1, 0);
    real_destroy(s->p);
    free(s->bytes);
    free(s);
  }
  if (clip_device) {
    real_destroy(clip_device);
    clip_device = 0;
  }
  if (clip_manager) {
    real_destroy(clip_manager);
    clip_manager = 0;
  }
  clip_serial = 0;
  clip_dirty = clip_current != 0;
}
