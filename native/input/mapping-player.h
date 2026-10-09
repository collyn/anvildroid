#ifndef ANVIL_MAPPING_PLAYER_H
#define ANVIL_MAPPING_PLAYER_H
#include "touch-session.h"
#define MAPPING_BINDINGS 64
#define MAPPING_KEYS 768
#define MAPPING_TAP_NS UINT64_C(35000000)
#ifndef ANVIL_MAPPING_BINDING_DEFINED
#define ANVIL_MAPPING_BINDING_DEFINED
struct mapping_binding { uint16_t key, nx, ny; int hold; };
#endif
struct mapping_player {
  struct touch_session session;
  struct mapping_binding bindings[MAPPING_BINDINGS];
  int count;
  uint32_t down[MAPPING_KEYS/32], captured[MAPPING_KEYS/32];
  uint64_t tap_deadline[MAPPING_BINDINGS];
};
/* Offline policy, not an injection authority. Caller supplies an authenticated,
 * focused Android route and a verified sink; no production hook is enabled here.
 * All calls must be serialized by the owner. */
int mapping_configure(struct mapping_player *, const struct mapping_binding *,
                      int count,uint64_t now,touch_sink,void *);
int mapping_route(struct mapping_player *,struct touch_route,int focused,
                  int stable,uint64_t now,touch_sink,void *);
/* Returns whether this physical event belongs to a mapping. Captured UP events
 * remain consumed across blur/failure even when new DOWN events are disabled. */
int mapping_key(struct mapping_player *,uint32_t key,int pressed,uint64_t now,
                touch_sink,void *);
/* Tap is a bounded 35ms gesture, not a shell command or an auto-repeat. A late
 * tick releases immediately; the real receiver must also have a watchdog. */
int mapping_tick(struct mapping_player *,uint64_t now,touch_sink,void *);
int mapping_cancel(struct mapping_player *,uint64_t now,touch_sink,void *);
#endif
