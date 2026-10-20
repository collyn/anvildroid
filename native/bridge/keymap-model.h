#ifndef ANVIL_KEYMAP_MODEL_H
#define ANVIL_KEYMAP_MODEL_H
#include <stdint.h>
#define KEYMAP_POINTS 64
#define KEYMAP_PROFILE_LIMIT 16384
struct keymap_point {
  int key, nx, ny, hold;
  int kind, ex, ey, duration, repeats;
  int source, end_mode, radius, ramp_ms, look_width, look_height;
  int sensitivity_x, sensitivity_y, invert_y, activation;
  int up_key, left_key, down_key, right_key, walk_key, walk_radius;
  int suspend_key, drag_shot, skill_mode, cancel_key, cancel_x, cancel_y;
};
struct keymap_model {
  struct keymap_point points[KEYMAP_POINTS];
  int count, selected, placing, dirty, error;
};

enum { KEYMAP_BASIC, KEYMAP_DPAD, KEYMAP_SWIPE, KEYMAP_REPEAT,
       KEYMAP_SKILL, KEYMAP_MACRO, KEYMAP_AIM, KEYMAP_MOUSE_TOUCH, KEYMAP_LOOK, KEYMAP_WALK };
enum { KEYMAP_SKILL_RELEASE, KEYMAP_SKILL_IMMEDIATE, KEYMAP_SKILL_TOGGLE };
enum { KEYMAP_SOURCE_KEY, KEYMAP_SOURCE_MOUSE, KEYMAP_SOURCE_POINTER };
enum { KEYMAP_END_LEGACY, KEYMAP_END_RELEASE, KEYMAP_END_HOLD };
static __attribute__((unused)) const char *keymap_kind_name(int kind) {
  static const char *names[]={"Basic","WASD","Swipe","Repeat","Skill","Macro","Aim","Mouse touch","Look","Right-click walk"};
  return kind>=0&&kind<=KEYMAP_WALK?names[kind]:"Invalid";
}
/* Keyboard reservations include secondary actions, so one physical event can
 * never activate two controls. Zero secondary fields preserve legacy profiles. */
static int keymap_keyboard_key(int key) {
  return key>=2&&key<=767&&key!=60&&key!=61&&key!=62;
}
static int keymap_dpad_key(const struct keymap_point *p,int direction) {
  const int custom[]={p->up_key,p->left_key,p->down_key,p->right_key};
  const int defaults[]={17,30,31,32};
  return p->up_key?custom[direction]:defaults[direction];
}
static int keymap_key_reservations(const struct keymap_point *p,int keys[8]) {
  int n=0;
  if(p->kind==KEYMAP_DPAD)for(int d=0;d<4;++d)keys[n++]=keymap_dpad_key(p,d);
  else if(p->source==KEYMAP_SOURCE_KEY&&p->key)keys[n++]=p->key;
  if(p->walk_key)keys[n++]=p->walk_key;
  if(p->suspend_key)keys[n++]=p->suspend_key;
  if(p->cancel_key)keys[n++]=p->cancel_key;
  return n;
}
static int keymap_valid(const struct keymap_model *m, int assigned) {
  if (!m || m->count < 0 || m->count > KEYMAP_POINTS)
    return 0;
  for (int i = 0; i < m->count; ++i) {
    const struct keymap_point *p = &m->points[i];
    if (p->key < 0 || p->key > 767 || (assigned && !p->key && p->source!=KEYMAP_SOURCE_POINTER) || p->key == 1 ||
        (p->source==KEYMAP_SOURCE_KEY && p->key && !keymap_keyboard_key(p->key)) ||
        p->nx < 0 || p->nx > 65535 || p->ny < 0 || p->ny > 65535 ||
        (p->hold != 0 && p->hold != 1) || p->kind<0 || p->kind>KEYMAP_WALK ||
        p->ex<0 || p->ex>65535 || p->ey<0 || p->ey>65535 ||
        p->duration<0 || p->duration>5000 || p->repeats<0 || p->repeats>20 ||
        (p->kind && (p->duration<50 || p->duration>5000 || p->repeats<1 || p->repeats>20)))
      return 0;
    if (p->source<0 || p->source>2 || p->end_mode<0 || p->end_mode>2 ||
        (p->radius && (p->radius<256 || p->radius>32767)) || p->ramp_ms<0 || p->ramp_ms>1000 ||
        p->look_width<0 || p->look_width>32767 || p->look_height<0 || p->look_height>32767 ||
        p->sensitivity_x<0 || p->sensitivity_x>1000 || p->sensitivity_y<0 || p->sensitivity_y>1000 ||
        p->invert_y<0 || p->invert_y>1 || p->activation<0 || p->activation>1) return 0;
    if (p->source==KEYMAP_SOURCE_MOUSE && (p->key<272 || p->key>276)) return 0;
    if ((p->source==KEYMAP_SOURCE_POINTER)!=(p->kind==KEYMAP_MOUSE_TOUCH) ||
        (p->source==KEYMAP_SOURCE_POINTER && p->key!=0)) return 0;
    if (p->kind==KEYMAP_LOOK && (p->look_width<256 || p->look_height<256 ||
        p->nx<p->look_width || p->nx+p->look_width>65535 ||
        p->ny<p->look_height || p->ny+p->look_height>65535 ||
        p->sensitivity_x<10 || p->sensitivity_y<10)) return 0;
    if (p->kind==KEYMAP_MACRO && p->duration*p->repeats*2>10000) return 0;
    if (p->kind==KEYMAP_DPAD && (p->key!=keymap_dpad_key(p,0) || p->source!=KEYMAP_SOURCE_KEY)) return 0;
    if(p->up_key||p->left_key||p->down_key||p->right_key) {
      if(p->kind!=KEYMAP_DPAD||!p->up_key||!p->left_key||!p->down_key||!p->right_key)return 0;
    }
    if(p->walk_key && p->kind!=KEYMAP_DPAD)return 0;
    if(p->walk_radius && (!p->walk_key||p->walk_radius<10||p->walk_radius>100))return 0;
    if(p->suspend_key && (p->kind!=KEYMAP_LOOK||!p->activation))return 0;
    if(p->drag_shot<0||p->drag_shot>1 || (p->drag_shot &&
       (p->kind!=KEYMAP_BASIC||p->source!=KEYMAP_SOURCE_MOUSE||
        !(p->end_mode==KEYMAP_END_HOLD||(p->end_mode==KEYMAP_END_LEGACY&&p->hold)))))return 0;
    if(p->skill_mode<0||p->skill_mode>KEYMAP_SKILL_TOGGLE||
       ((p->skill_mode||p->cancel_key||p->cancel_x||p->cancel_y)&&p->kind!=KEYMAP_SKILL)||
       p->cancel_x<0||p->cancel_x>65535||p->cancel_y<0||p->cancel_y>65535||
       ((p->cancel_x||p->cancel_y)&&!p->cancel_key))return 0;
    if(p->kind==KEYMAP_WALK && (p->source!=KEYMAP_SOURCE_MOUSE||p->key!=273||!p->radius))return 0;
    int keys[8],nkeys=keymap_key_reservations(p,keys);
    for(int a=0;a<nkeys;++a) {
      if(!keymap_keyboard_key(keys[a]))return 0;
      for(int b=0;b<a;++b)if(keys[a]==keys[b])return 0;
    }
    for (int j = 0; j < m->count; ++j) {
      const struct keymap_point *q=&m->points[j];
      if (i==j) continue;
      int other[8],nother=keymap_key_reservations(q,other);
      for(int a=0;a<nkeys;++a)for(int b=0;b<nother;++b)if(keys[a]==other[b])return 0;
      if(p->drag_shot&&q->drag_shot)return 0;
      if ((p->kind==KEYMAP_MOUSE_TOUCH || p->kind==KEYMAP_LOOK) && q->kind==p->kind) return 0;
      if (p->kind==KEYMAP_AIM && q->source==KEYMAP_SOURCE_MOUSE && q->key==272) return 0;
      if (p->key && p->source==q->source && p->key==q->key) return 0;
    }
  }
  return 1;
}
/* Convert the persisted editor model to the bounded runtime representation. */
#ifndef ANVIL_MAPPING_BINDING_DEFINED
#define ANVIL_MAPPING_BINDING_DEFINED
struct mapping_binding { uint16_t key, nx, ny; int hold; };
#endif
static int __attribute__((unused))
keymap_bindings(const struct keymap_model *m, struct mapping_binding *out,
                int capacity) {
  if (!m || !out || capacity < m->count || !keymap_valid(m, 1)) return 0;
  for (int i = 0; i < m->count; ++i) if(m->points[i].kind || m->points[i].source) return 0;
  for (int i = 0; i < m->count; ++i) {
    out[i].key = (uint16_t)m->points[i].key;
    out[i].nx = (uint16_t)m->points[i].nx;
    out[i].ny = (uint16_t)m->points[i].ny;
    out[i].hold = m->points[i].hold;
  }
  return m->count;
}
static int keymap_normalize(int p, int size) {
  if (p < 0)
    p = 0;
  if (p >= size)
    p = size - 1;
  return size > 1 ? (int)(((int64_t)p * 65535 + (size - 1) / 2) / (size - 1))
                  : 0;
}
static void keymap_move(struct keymap_model *m, int i, int x, int y, int w,
                        int h) {
  if (i < 0 || i >= m->count || w < 2 || h < 2)
    return;
  m->points[i].nx = keymap_normalize(x, w);
  m->points[i].ny = keymap_normalize(y, h);
  if (m->points[i].kind==KEYMAP_LOOK) {
    struct keymap_point *p=&m->points[i];
    if(p->nx<p->look_width)p->nx=p->look_width;
    if(p->nx>65535-p->look_width)p->nx=65535-p->look_width;
    if(p->ny<p->look_height)p->ny=p->look_height;
    if(p->ny>65535-p->look_height)p->ny=65535-p->look_height;
  }
  m->dirty = 1;
}
static int keymap_add(struct keymap_model *m, int x, int y, int w, int h) {
  if (m->count == KEYMAP_POINTS)
    return 0;
  m->selected = m->count++;
  m->points[m->selected] = (struct keymap_point){0};
  keymap_move(m, m->selected, x, y, w, h);
  m->placing = 0;
  m->error = 0;
  return 1;
}
static void keymap_set_kind(struct keymap_point *p, int kind) {
  /* Changing the gesture must not reinterpret a mouse button as a keyboard
   * evdev code. Pointer-only controls have no reusable trigger. */
  int source=p->source;
  p->kind=kind; p->source=source==KEYMAP_SOURCE_MOUSE?source:KEYMAP_SOURCE_KEY;
  if(source==KEYMAP_SOURCE_POINTER)p->key=0;
  p->up_key=p->left_key=p->down_key=p->right_key=p->walk_key=p->walk_radius=0;
  p->suspend_key=p->drag_shot=p->skill_mode=p->cancel_key=p->cancel_x=p->cancel_y=0;
  p->end_mode=KEYMAP_END_LEGACY;
  p->radius=p->ramp_ms=p->look_width=p->look_height=0;
  p->sensitivity_x=p->sensitivity_y=p->invert_y=p->activation=0;
  p->duration=250; p->repeats=3;
  p->ex=p->nx>49151?65535:p->nx+16384; p->ey=p->ny;
  if (kind==KEYMAP_BASIC) { p->end_mode=KEYMAP_END_RELEASE; p->duration=50; }
  if (kind==KEYMAP_SWIPE) p->end_mode=KEYMAP_END_RELEASE;
  if (kind==KEYMAP_DPAD) { p->source=KEYMAP_SOURCE_KEY; p->key=17; p->radius=8192; p->ramp_ms=80; }
  if (kind==KEYMAP_SKILL) p->radius=8192;
  if (kind==KEYMAP_WALK) { p->source=KEYMAP_SOURCE_MOUSE;p->key=273;p->radius=8192; }
  if (kind==KEYMAP_MOUSE_TOUCH) { p->source=KEYMAP_SOURCE_POINTER; p->key=0; }
  if (kind==KEYMAP_LOOK) {
    if(p->nx<256)p->nx=256;
    if(p->nx>65279)p->nx=65279;
    if(p->ny<256)p->ny=256;
    if(p->ny>65279)p->ny=65279;
    p->look_width=p->nx<32768?p->nx:65535-p->nx;
    p->look_height=p->ny<32768?p->ny:65535-p->ny;
    if(p->look_width>8192)p->look_width=8192;
    if(p->look_height>8192)p->look_height=8192;
    p->sensitivity_x=p->sensitivity_y=100;
  }
}
static int keymap_assign_source(struct keymap_model *m, int source, int key) {
  if (m->selected<0 || m->selected>=m->count || source<0 || source>2 ||
      (source==KEYMAP_SOURCE_KEY && (key<2 || key>767 || (key>=60 && key<=62))) ||
      (source==KEYMAP_SOURCE_MOUSE && (key<272 || key>276)) ||
      (source==KEYMAP_SOURCE_POINTER && key!=0)) {m->error=2;return 0;}
  struct keymap_point old=m->points[m->selected];
  m->points[m->selected].source=source; m->points[m->selected].key=key;
  if (!keymap_valid(m,0)) { m->points[m->selected]=old; m->error=1; return 0; }
  m->dirty=1; m->error=0; return 1;
}
static int __attribute__((unused)) keymap_assign(struct keymap_model *m, int key) {
  return keymap_assign_source(m,KEYMAP_SOURCE_KEY,key);
}
static void keymap_remove(struct keymap_model *m) {
  if (m->selected < 0 || m->selected >= m->count)
    return;
  for (int i = m->selected + 1; i < m->count; ++i)
    m->points[i - 1] = m->points[i];
  --m->count;
  m->selected = -1;
  m->dirty = 1;
  m->error = 0;
}
#endif
