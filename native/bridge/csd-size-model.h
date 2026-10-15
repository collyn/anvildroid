#ifndef ANVILDROID_CSD_SIZE_MODEL_H
#define ANVILDROID_CSD_SIZE_MODEL_H
/* Logical content dimensions shared by app windows and the Android canvas. */
static const struct {int width,height;const char *label;} csd_menu_sizes[]={
  {480,800,"480 x 800"},{600,960,"600 x 960"},
  {720,1280,"720 x 1280"},{1280,720,"1280 x 720"}
};
#endif
