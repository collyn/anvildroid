#include "text-state.h"
#ifdef __ANDROID__
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
#else
#include <string.h>
#endif

int anvil_ime_utf16_index(const char *text, size_t index, int *units) {
  size_t length = strlen(text), at = 0;
  int count = 0;
  if (index > length || length > ANVIL_IME_TEXT_CAP)
    return 0;
  while (at < index) {
    unsigned char first = (unsigned char)text[at];
    size_t n;
    uint32_t cp;
    if (first < 0x80) {
      n = 1;
      cp = first;
    } else if (first >= 0xc2 && first <= 0xdf) {
      n = 2;
      cp = first & 0x1f;
    } else if (first >= 0xe0 && first <= 0xef) {
      n = 3;
      cp = first & 0x0f;
    } else if (first >= 0xf0 && first <= 0xf4) {
      n = 4;
      cp = first & 7;
    } else
      return 0;
    if (at + n > index)
      return 0;
    for (size_t j = 1; j < n; ++j) {
      unsigned char c = (unsigned char)text[at + j];
      if ((c & 0xc0) != 0x80)
        return 0;
      cp = (cp << 6) | (c & 0x3f);
    }
    if ((n == 2 && cp < 0x80) || (n == 3 && cp < 0x800) ||
        (n == 4 && cp < 0x10000) || cp > 0x10ffff ||
        (cp >= 0xd800 && cp <= 0xdfff))
      return 0;
    count += cp > 0xffff ? 2 : 1;
    at += n;
  }
  *units = count;
  return 1;
}

int anvil_ime_delete_units(const char *text, size_t cursor, size_t anchor,
                           size_t before, size_t after, int *before_units,
                           int *after_units) {
  size_t start = cursor < anchor ? cursor : anchor;
  size_t end = cursor > anchor ? cursor : anchor;
  size_t length = strlen(text);
  int a, b, c, d, valid;
  if (end > length || before > start || after > length - end ||
      !anvil_ime_utf16_index(text, length, &valid) ||
      !anvil_ime_utf16_index(text, start - before, &a) ||
      !anvil_ime_utf16_index(text, start, &b) ||
      !anvil_ime_utf16_index(text, end, &c) ||
      !anvil_ime_utf16_index(text, end + after, &d))
    return 0;
  *before_units = b - a;
  *after_units = d - c;
  return 1;
}

static void reset_pending(struct anvil_ime_state *s) {
  memset(&s->pending, 0, sizeof(s->pending));
  s->invalid = 0;
}
void anvil_ime_focus(struct anvil_ime_state *s, int editable) {
  ++s->generation;
  s->active = editable != 0;
  reset_pending(s);
}
static int copy_text(struct anvil_ime_state *s, char *dest, const char *text) {
  if (!s->active || s->invalid)
    return 0;
  if (!text)
    text = "";
  size_t length = strlen(text);
  int units;
  if (!anvil_ime_utf16_index(text, length, &units)) {
    s->invalid = 1;
    return 0;
  }
  memcpy(dest, text, length + 1);
  return 1;
}
int anvil_ime_preedit(struct anvil_ime_state *s, const char *text, int begin,
                      int end) {
  if (!copy_text(s, s->pending.preedit, text))
    return 0;
  if (begin == -1 && end == -1) {
    s->pending.cursor_begin = s->pending.cursor_end = -1;
    return 1;
  }
  if (begin < 0 || end < 0 ||
      !anvil_ime_utf16_index(s->pending.preedit, (size_t)begin,
                             &s->pending.cursor_begin) ||
      !anvil_ime_utf16_index(s->pending.preedit, (size_t)end,
                             &s->pending.cursor_end)) {
    s->invalid = 1;
    return 0;
  }
  return 1;
}
int anvil_ime_commit(struct anvil_ime_state *s, const char *text) {
  return copy_text(s, s->pending.commit, text);
}
void anvil_ime_delete(struct anvil_ime_state *s, uint32_t before,
                      uint32_t after) {
  if (!s->active || s->invalid)
    return;
  s->pending.delete_before_bytes = before;
  s->pending.delete_after_bytes = after;
}
int anvil_ime_done(struct anvil_ime_state *s, uint32_t serial,
                   uint32_t client_commits, struct anvil_ime_batch *out) {
  int deliver = s->active && !s->invalid;
  if (deliver) {
    *out = s->pending;
    out->generation = s->generation;
    out->refresh_wayland_state = serial == client_commits;
  }
  reset_pending(s);
  return deliver;
}
