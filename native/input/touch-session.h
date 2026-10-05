#ifndef ANVIL_TOUCH_SESSION_H
#define ANVIL_TOUCH_SESSION_H
#include <stdint.h>
#define ANVIL_TOUCH_CONTACTS 10
/* Bounds are already verified Android display pixels, excluding decorations.
 * Opaque tokens identify one runtime and one surface lifetime, never a package
 * guessed from whichever window happens to be focused. */
struct touch_route {
  uint64_t runtime, surface;
  int x, y, width, height;
};
struct touch_point { int id, x, y; uint32_t binding; };
enum touch_action { TOUCH_PRESS, TOUCH_MOVE, TOUCH_RELEASE, TOUCH_CANCEL };
struct touch_frame {
  struct touch_route route;
  uint64_t epoch, sequence, time_ns, down_time_ns;
  enum touch_action action;
  int changed_id, count;
  struct touch_point points[ANVIL_TOUCH_CONTACTS];
};
/* Must synchronously accept the entire frame or report failure. A failed send
 * is uncertain delivery; the session latches fault until receiver reset is
 * confirmed. No transport/injection implementation is provided here. */
typedef int (*touch_sink)(void *, const struct touch_frame *);
struct touch_session {
  struct touch_route route;
  uint64_t epoch, sequence, last_ns, down_ns;
  int enabled, fault, count;
  struct touch_point points[ANVIL_TOUCH_CONTACTS];
};
int touch_bind(struct touch_session *, struct touch_route);
int touch_press(struct touch_session *, uint64_t epoch, uint32_t binding,
                uint16_t nx, uint16_t ny, uint64_t now_ns, touch_sink, void *);
int touch_move(struct touch_session *, uint64_t epoch, uint32_t binding,
               uint16_t nx, uint16_t ny, uint64_t now_ns, touch_sink, void *);
int touch_release(struct touch_session *, uint64_t epoch, uint32_t binding,
                  uint64_t now_ns, touch_sink, void *);
/* Use for focus loss, IME/text focus, resize, disable, close and disconnect. */
int touch_suspend(struct touch_session *, uint64_t now_ns, touch_sink, void *);
/* Only after the backend confirms all contacts cleared in the old receiver.
 * Clearing a local flag alone must never be treated as that acknowledgement. */
void touch_receiver_reset_confirmed(struct touch_session *);
/* Called with trusted geometry/focus observations. Any in-progress resize or
 * invalid/lost focus suspends input. New stable bounds require cancellation on
 * the old route before a fresh epoch; held keys are not replayed automatically. */
int touch_reconfigure(struct touch_session *, struct touch_route, int focused,
                      int geometry_stable, uint64_t now_ns, touch_sink, void *);
#endif
