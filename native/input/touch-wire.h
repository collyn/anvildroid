#ifndef ANVIL_TOUCH_WIRE_H
#define ANVIL_TOUCH_WIRE_H
#include "touch-session.h"
#include <stddef.h>
#define TOUCH_WIRE_MAX (88 + 16 * ANVIL_TOUCH_CONTACTS)
/* Explicit little-endian v1 frame; never transmit native struct padding. */
size_t touch_encode(const struct touch_frame *, unsigned char *, size_t);
int touch_decode(const unsigned char *, size_t, struct touch_frame *);
/* Connected local SOCK_SEQPACKET only; verify peer UID before any IO.
 * Nonblocking operations; failure/oversize is not partial success. */
int touch_packet_send(int fd, unsigned int expected_uid, const struct touch_frame *);
int touch_packet_receive(int fd, unsigned int expected_uid, struct touch_frame *);

struct touch_receiver {
  struct touch_route route;
  uint64_t epoch, sequence, time_ns, down_time_ns;
  int focused, fault, count;
  struct touch_point points[ANVIL_TOUCH_CONTACTS];
};
/* Only trusted local focus/handshake logic supplies this scope. Never call with
 * route/epoch read from an untrusted packet. Caller starts with zeroed state. */
int touch_receiver_bind(struct touch_receiver *, struct touch_route, uint64_t epoch, uint64_t prior_sequence);
void touch_receiver_blur(struct touch_receiver *);
/* Validate transition BEFORE delivering, commit state only on sink success.
 * Invalid/stale packet or sink failure latches fault. Retain contacts until
 * the real backend resets them; memset is NOT a receiver-reset protocol. */
int touch_receiver_apply(struct touch_receiver *, const struct touch_frame *, touch_sink, void *);
/* Local watchdog/disconnect path: cancel held contacts on the OLD route,
 * without waiting for a packet from the vanished sender. A new authenticated
 * bind with a strictly newer epoch is required afterward. */
int touch_receiver_disconnect(struct touch_receiver *, uint64_t now_ns, touch_sink, void *);
#endif
