#ifndef ANVILDROID_RESIZE_REQUEST_H
#define ANVILDROID_RESIZE_REQUEST_H
#include <stddef.h>
extern int snprintf(char *, size_t, const char *, ...);

/* A real newline is required by the companion's read -r and numeric checks. */
static int format_resize_request(char *buffer, size_t size, int task, int width,
                                 int height) {
  if (task < 0 || width < 64 || height < 64 || width > 8192 || height > 8192)
    return -1;
  int length = snprintf(buffer, size, "%d %d %d\n", task, width, height);
  return length >= 0 && (size_t)length < size ? length : -1;
}
#endif
