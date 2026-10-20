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
  /* V4 reserves all physical keyboard actions, including secondary keys. */
  m=(struct keymap_model){.count=1};
  m.points[0]=(struct keymap_point){.nx=32768,.ny=32768};
  keymap_set_kind(&m.points[0],KEYMAP_DPAD);
  struct keymap_point *p=&m.points[0];
  p->up_key=23;p->left_key=36;p->down_key=37;p->right_key=38;p->key=23;
  p->walk_key=42;p->walk_radius=40;
  assert(keymap_valid(&m,1));
  p->right_key=23;assert(!keymap_valid(&m,1));p->right_key=38;
  p->walk_key=23;assert(!keymap_valid(&m,1));p->walk_key=42;
  m.count=2;m.points[1]=(struct keymap_point){.key=42};assert(!keymap_valid(&m,1));
  m.points[1].key=57;assert(keymap_valid(&m,1));
  keymap_set_kind(&m.points[1],KEYMAP_LOOK);m.points[1].activation=1;m.points[1].suspend_key=42;
  assert(!keymap_valid(&m,1));m.points[1].suspend_key=56;assert(keymap_valid(&m,1));
  for(int reserved=60;reserved<=62;++reserved){m.points[1].suspend_key=reserved;assert(!keymap_valid(&m,1));}
  m.points[1].suspend_key=56;
  p->up_key=0;assert(!keymap_valid(&m,1));
  keymap_set_kind(p,KEYMAP_WALK);assert(p->key==273&&p->source==KEYMAP_SOURCE_MOUSE&&p->radius==8192);
  assert(keymap_valid(&m,1));
  p->drag_shot=1;assert(!keymap_valid(&m,1));
  return 0;
}
