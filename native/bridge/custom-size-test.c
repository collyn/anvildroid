#include "custom-size.h"
#include <assert.h>
int main(void) {
  struct custom_size s={.enabled=1};int w,h;
  custom_size_queue(&s,800,1000,1);
  for(int i=0;i<20;++i)assert(!custom_size_due(&s,1280,1280,&w,&h));
  custom_size_queue(&s,900,1100,0);
  for(int i=0;i<5;++i)assert(!custom_size_due(&s,1280,1280,&w,&h));
  custom_size_queue(&s,1000,1200,0); /* newest configure wins */
  for(int i=0;i<5;++i)assert(!custom_size_due(&s,1280,1280,&w,&h));
  assert(custom_size_due(&s,1280,1280,&w,&h)&&w==1000&&h==1200);
  assert(!custom_size_due(&s,1280,1280,&w,&h));
  custom_size_queue(&s,1920,1080,0);
  for(int i=0;i<5;++i)assert(!custom_size_due(&s,1280,1280,&w,&h));
  assert(custom_size_due(&s,1280,1280,&w,&h)&&w==1280&&h==720);
  s.enabled=0;custom_size_queue(&s,800,1000,0);
  for(int i=0;i<20;++i)assert(!custom_size_due(&s,1280,1280,&w,&h));
  return 0;
}
