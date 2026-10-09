#ifndef ANVIL_KEYMAP_MODEL_H
#define ANVIL_KEYMAP_MODEL_H
#include <stdint.h>
#define KEYMAP_POINTS 64
struct keymap_point {
  int key, nx, ny, hold;
};
struct keymap_model {
  struct keymap_point points[KEYMAP_POINTS];
  int count, selected, placing, dirty, error;
};

static int keymap_valid(const struct keymap_model *m, int assigned) {
  if (m->count < 0 || m->count > KEYMAP_POINTS)
    return 0;
  for (int i = 0; i < m->count; ++i) {
    const struct keymap_point *p = &m->points[i];
    if (p->key < 0 || p->key > 767 || (assigned && !p->key) || p->key == 1 ||
        p->nx < 0 || p->nx > 65535 || p->ny < 0 || p->ny > 65535 ||
        (p->hold != 0 && p->hold != 1))
      return 0;
    for (int j = 0; j < i; ++j)
      if (p->key && p->key == m->points[j].key)
        return 0;
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
static int keymap_assign(struct keymap_model *m, int key) {
  if (m->selected < 0 || m->selected >= m->count || key < 2 || key > 767 ||
      (key >= 60 && key <= 62))
    return 0;
  for (int i = 0; i < m->count; ++i)
    if (i != m->selected && m->points[i].key == key) {
      m->error = 1;
      return 0;
    }
  m->points[m->selected].key = key;
  m->dirty = 1;
  m->error = 0;
  return 1;
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
