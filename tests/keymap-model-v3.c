#include "../native/bridge/keymap-model.h"
#include <assert.h>
int main(void) {
  struct keymap_model m={.selected=-1};
  assert(keymap_add(&m,100,100,800,600));
  assert(keymap_assign(&m,30));
  keymap_set_kind(&m.points[0],KEYMAP_LOOK);
  assert(keymap_valid(&m,1));
  assert(m.points[0].sensitivity_x==100);
  assert(keymap_assign_source(&m,KEYMAP_SOURCE_MOUSE,273));
  assert(keymap_add(&m,0,0,800,600));
  keymap_set_kind(&m.points[1],KEYMAP_MOUSE_TOUCH);
  assert(keymap_valid(&m,1));
  assert(!keymap_assign(&m,30));
  keymap_remove(&m);
  m.points[0].nx=0;
  assert(!keymap_valid(&m,1));
  keymap_set_kind(&m.points[0],KEYMAP_LOOK);
  assert(keymap_valid(&m,1));
  return 0;
}
