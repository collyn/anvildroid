#ifndef ANVILDROID_KEYMAP_FONT_H
#define ANVILDROID_KEYMAP_FONT_H
/* Small original 5x7 raster alphabet for the in-process editor toolbar. */
static void keymap_text_scaled(uint32_t *pixels, int w, int h, int x, int y,
                        const char *text, uint32_t color, int scale) {
  static const unsigned char letters[39][7] = {
      {14, 17, 17, 31, 17, 17, 17}, {30, 17, 17, 30, 17, 17, 30},
      {14, 17, 16, 16, 16, 17, 14}, {30, 17, 17, 17, 17, 17, 30},
      {31, 16, 16, 30, 16, 16, 31}, {31, 16, 16, 30, 16, 16, 16},
      {14, 17, 16, 23, 17, 17, 15}, {17, 17, 17, 31, 17, 17, 17},
      {14, 4, 4, 4, 4, 4, 14},      {7, 2, 2, 2, 18, 18, 12},
      {17, 18, 20, 24, 20, 18, 17}, {16, 16, 16, 16, 16, 16, 31},
      {17, 27, 21, 21, 17, 17, 17}, {17, 25, 21, 19, 17, 17, 17},
      {14, 17, 17, 17, 17, 17, 14}, {30, 17, 17, 30, 16, 16, 16},
      {14, 17, 17, 17, 21, 18, 13}, {30, 17, 17, 30, 20, 18, 17},
      {15, 16, 16, 14, 1, 1, 30},   {31, 4, 4, 4, 4, 4, 4},
      {17, 17, 17, 17, 17, 17, 14}, {17, 17, 17, 17, 17, 10, 4},
      {17, 17, 17, 21, 21, 27, 17}, {17, 17, 10, 4, 10, 17, 17},
      {17, 17, 10, 4, 4, 4, 4},     {31, 1, 2, 4, 8, 16, 31},
      {14, 17, 19, 21, 25, 17, 14}, {4, 12, 4, 4, 4, 4, 14},
      {14, 17, 1, 2, 4, 8, 31},     {30, 1, 1, 14, 1, 1, 30},
      {2, 6, 10, 18, 31, 2, 2},     {31, 16, 16, 30, 1, 1, 30},
      {14, 16, 16, 30, 17, 17, 14}, {31, 1, 2, 4, 8, 8, 8},
      {14, 17, 17, 14, 17, 17, 14}, {14, 17, 17, 15, 1, 1, 14},
      {0,0,0,0,0,4,4}, {0,0,0,31,0,0,0}, {0,4,4,31,4,4,0}};
  for (; *text; ++text, x += 6 * scale) {
    int index = *text >= 'A' && *text <= 'Z'   ? *text - 'A'
                : *text >= '0' && *text <= '9' ? 26 + *text - '0'
                : *text == '.' ? 36 : *text == '-' ? 37 : *text == '+' ? 38 : -1;
    if (index < 0)
      continue;
    for (int row = 0; row < 7; ++row)
      for (int col = 0; col < 5; ++col)
        if (letters[index][row] & (1 << (4 - col)))
          for (int dy = 0; dy < scale; ++dy)
            for (int dx = 0; dx < scale; ++dx) {
              int px = x + col * scale + dx, py = y + row * scale + dy;
              if (px >= 0 && px < w && py >= 0 && py < h)
                pixels[(size_t)py * w + px] = color;
            }
  }
}
static void keymap_text(uint32_t *pixels, int w, int h, int x, int y,
                        const char *text, uint32_t color) {
  keymap_text_scaled(pixels,w,h,x,y,text,color,2);
}
#endif
