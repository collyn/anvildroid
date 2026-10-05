#ifndef ANVILDROID_RESIZE_POLICY_H
#define ANVILDROID_RESIZE_POLICY_H

/* Let a release settle for 300-350ms before relayout. Never resize Android
 * while the pointer is held, even if an old caller requests live_drag. */
#define RESIZE_POLL_US 50000
#define RESIZE_QUIET_TICKS 6
#define RESIZE_COOLDOWN_TICKS 20
/* Maximize/restore transitions wait extra quiet ticks: the toggle itself
 * churns fragile apps (Cocos/ARM games), so let the double-click's pointer
 * activity settle before the relayout request reaches Android. */
#define RESIZE_STATE_QUIET_EXTRA 4

struct resize_policy {
  int width, height, dragging, dirty, quiet_ticks, ready, maximized, live_drag;
  int cooldown, sent_width, sent_height;
};

/* xdg_toplevel states: maximized=1, fullscreen=2, resizing=3.
 * A pause with the button held must never recreate an Android activity. */
static void resize_configure(struct resize_policy *p, int width, int height,
                             int current_width, int current_height,
                             int resizing, int maximized) {
  int was_dragging = p->dragging;
  int state_changed = maximized != p->maximized;
  p->dragging = resizing;
  p->maximized = maximized;
  if (width < 64 || height < 64 || width > 8192 || height > 8192)
    return;
  if (width == current_width && height == current_height && !state_changed) {
    p->ready = 1;
    p->width = p->height = 0; /* User dragged back to the original size. */
    return;
  }
  /* A maximize/restore landing on the size already delivered to Android is a
   * no-op, not a retry: re-pushing an unchanged size relayouts fragile apps
   * (Cocos/ARM games) for nothing. A resize that is still pending/in-flight
   * keeps its supersede semantics below. */
  if (width == current_width && height == current_height && !p->width && !p->height)
    return;
  /* Ignore a stale display-sized configure during initial window mapping. */
  if (!p->ready && !resizing && !was_dragging && !state_changed)
    return;
  p->ready = 1;
  /* Configure echoes may arrive before the task snapshot catches up. Do not
   * submit the same relayout twice during the recovery interval. */
  if (p->cooldown && width == p->sent_width && height == p->sent_height) {
    p->width = p->height = 0;
    return;
  }
  /* A state transition must supersede a resize already handed to Android,
   * even if the old task snapshot still equals the requested restore size. */
  if (p->width != width || p->height != height || was_dragging != resizing || state_changed) {
    p->width = width;
    p->height = height;
    p->dirty = 1;
    p->quiet_ticks = state_changed ? -RESIZE_STATE_QUIET_EXTRA : 0;
  }
}

static int resize_due(struct resize_policy *p) {
  int cooling = p->cooldown > 0;
  if (cooling) --p->cooldown;
  if (!p->width || p->dragging)
    return 0;
  if (p->dirty) {
    p->dirty = 0;
    if (p->quiet_ticks < 0) return 0; /* extended quiet for state transitions */
    p->quiet_ticks = 0;
    return 0;
  }
  if (p->quiet_ticks < RESIZE_QUIET_TICKS)
    ++p->quiet_ticks;
  return !cooling && p->quiet_ticks == RESIZE_QUIET_TICKS;
}

/* Reserve a recovery interval before another task relayout. New requests
 * coalesce to the latest size rather than forming a queue of recreations. */
static inline void resize_submitted(struct resize_policy *p) {
  p->sent_width = p->width; p->sent_height = p->height;
  p->width = p->height = 0;
  p->cooldown = RESIZE_COOLDOWN_TICKS;
}

/* Mutter may omit a final xdg_toplevel.configure after the pointer release.
 * The CSD bridge has the authoritative button-release serial, so end the
 * interactive phase explicitly and let the worker submit the last size. */
static void resize_release(struct resize_policy *p) {
  if (!p || !p->dragging) return;
  p->dragging = 0;
  if (p->width && p->height) {
    p->dirty = 1;
    p->quiet_ticks = 0;
  }
}
#endif
