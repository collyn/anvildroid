#ifndef ANVILDROID_CSD_LAYOUT_H
#define ANVILDROID_CSD_LAYOUT_H
/* Logical surface coordinates, not Android display coordinates. */
#define CSD_BORDER 2   /* Visible frame thickness. */
#define CSD_GRIP 6     /* Invisible top resize grip inside the header. */
#define CSD_HEADER 38
#define CSD_BUTTON 28
enum { CSD_MOVE = 100, CSD_MINIMIZE, CSD_MAXIMIZE, CSD_CLOSE };
static inline int csd_button_center(int action, int width) {
  return width - CSD_BORDER - CSD_BUTTON / 2 - (CSD_CLOSE - action) * CSD_BUTTON;
}
static int csd_hit(int piece, int x, int y, int width, int height) {
  if (x < 0 || y < 0 || x >= width || y >= height) return 0;
  if (piece == 0) {
    if (y < CSD_GRIP) return x < 16 ? 5 : x >= width - 16 ? 9 : 1;
    if (x < CSD_BORDER) return 4;
    if (x >= width-CSD_BORDER) return 8;
    if (x >= width - CSD_BORDER - CSD_BUTTON) return CSD_CLOSE;
    if (x >= width - CSD_BORDER - 2 * CSD_BUTTON) return CSD_MAXIMIZE;
    if (x >= width - CSD_BORDER - 3 * CSD_BUTTON) return CSD_MINIMIZE;
    return CSD_MOVE;
  }
  if (piece == 1) return y >= height - 16 ? 6 : 4;
  if (piece == 2) return y >= height - 16 ? 10 : 8;
  if (piece == 3) return x < 16 ? 6 : x >= width - 16 ? 10 : 2;
  return 0;
}
#endif
