#ifndef ANVILDROID_CSD_LAYOUT_H
#define ANVILDROID_CSD_LAYOUT_H
/* Logical surface coordinates, not Android display coordinates. */
#define CSD_BORDER 6
#define CSD_HEADER 38
#define CSD_BUTTON 36
enum { CSD_MOVE = 100, CSD_MINIMIZE, CSD_MAXIMIZE, CSD_CLOSE };
static int csd_hit(int piece, int x, int y, int width, int height) {
  if (x < 0 || y < 0 || x >= width || y >= height) return 0;
  if (piece == 0) {
    if (y < CSD_BORDER) return x < 16 ? 5 : x >= width - 16 ? 9 : 1;
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
