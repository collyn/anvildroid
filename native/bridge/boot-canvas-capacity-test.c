#include "boot-canvas.h"
#include <assert.h>
int main(void) {
  int w,h;
  assert(boot_canvas_capacity(1920,1036,120,&w,&h));assert(w==1984&&h==1280);
  assert(boot_canvas_capacity(1536,864,240,&w,&h));assert(w==800&&h==640);
  assert(boot_canvas_capacity(640,480,120,&w,&h));assert(w==1280&&h==1280);
  assert(boot_canvas_size(640,480,120,&w,&h));assert(w>=1280&&h>=1280);
  assert(!boot_canvas_capacity(8192,8192,120,&w,&h));
  return 0;
}
