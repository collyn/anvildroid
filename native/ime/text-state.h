#ifndef ANVILDROID_TEXT_STATE_H
#define ANVILDROID_TEXT_STATE_H
#include <stddef.h>
#include <stdint.h>

#define ANVIL_IME_TEXT_CAP 4096
struct anvil_ime_batch {
  uint64_t generation;
  char preedit[ANVIL_IME_TEXT_CAP + 1], commit[ANVIL_IME_TEXT_CAP + 1];
  int cursor_begin, cursor_end; /* UTF-16 units, or both -1 (hidden). */
  uint32_t delete_before_bytes, delete_after_bytes;
  int refresh_wayland_state;
};
struct anvil_ime_state {
  uint64_t generation;
  int active, invalid;
  struct anvil_ime_batch pending;
};

/* Reject malformed UTF-8 and offsets inside a code point. */
int anvil_ime_utf16_index(const char *text, size_t byte_index, int *units);
/* Snapshot excludes preedit; cursor/anchor and deletion lengths are UTF-8
 * bytes. Deletion excludes the selection. Outputs suit deleteSurroundingText().
 */
int anvil_ime_delete_units(const char *text, size_t cursor, size_t anchor,
                           size_t before, size_t after, int *before_units,
                           int *after_units);
/* Call on each Android editor change as well as host surface enter/leave. */
void anvil_ime_focus(struct anvil_ime_state *s, int editable);
int anvil_ime_preedit(struct anvil_ime_state *s, const char *text, int begin,
                      int end);
int anvil_ime_commit(struct anvil_ime_state *s, const char *text);
void anvil_ime_delete(struct anvil_ime_state *s, uint32_t before,
                      uint32_t after);
/* Output is valid only for generation's still-focused Android InputConnection.
 * A serial mismatch still delivers text; only Wayland state refresh is
 * deferred. */
int anvil_ime_done(struct anvil_ime_state *s, uint32_t serial,
                   uint32_t client_commits, struct anvil_ime_batch *out);
#endif
