/* Host-only stdin adapter for the production native profile parser. */
#include "../native/bridge/bridge.c"

int __android_log_print(int level, const char *tag, const char *format, ...) {
  (void)level; (void)tag; (void)format;
  return 0;
}

int main(void) {
  char bytes[KEYMAP_PROFILE_LIMIT + 2];
  long size = 0;
  while (size < KEYMAP_PROFILE_LIMIT + 1) {
    long count = read(0, bytes + size, KEYMAP_PROFILE_LIMIT + 1 - size);
    if (count < 0) return 2;
    if (!count) break;
    size += count;
  }
  if (size > KEYMAP_PROFILE_LIMIT) return 1;
  bytes[size] = 0;
  struct keymap_model model;
  return keymap_profile_parse(bytes, (int)size, "com.game.test", "r-one", &model) ? 0 : 1;
}
