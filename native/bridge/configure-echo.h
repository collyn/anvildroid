#ifndef ANVILDROID_CONFIGURE_ECHO_H
#define ANVILDROID_CONFIGURE_ECHO_H
/* The bridge sometimes chooses the host size itself (open size, profile
 * restore, Size menu) after the window was already published at another one.
 * Configures the compositor queued before it saw that frame still carry the
 * old size; accepting one as a user resize republishes the old frame. The X11
 * adapter configures every observed resize, so the old and new frames then
 * chase each other indefinitely (466x858 <-> 450x738 for Gallery). Remember
 * the superseded size until the compositor confirms any other size, a user
 * interaction starts, or a bounded wait (a WM that refused the frame) ends. */
#define CONFIGURE_ECHO_TICKS 40 /* ~2 s at the 50 ms task poll */

struct configure_echo { int width, height, ticks; };

static void configure_echo_supersede(struct configure_echo *e, int old_width,
                                     int old_height, int width, int height) {
  if (old_width < 64 || old_height < 64 ||
      (old_width == width && old_height == height)) return;
  e->width = old_width; e->height = old_height;
  e->ticks = CONFIGURE_ECHO_TICKS;
}
/* Content-sized configure. Returns 1 when it is the superseded frame. */
static int configure_echo_stale(struct configure_echo *e, int width, int height,
                                int interactive) {
  if (!e->width) return 0;
  if (!interactive && width == e->width && height == e->height) return 1;
  /* Zero is unspecified: neither a confirmation nor a user choice. */
  if (interactive || (width > 0 && height > 0))
    *e = (struct configure_echo){0};
  return 0;
}
static void configure_echo_tick(struct configure_echo *e) {
  if (e->ticks > 0 && !--e->ticks) *e = (struct configure_echo){0};
}
#endif
