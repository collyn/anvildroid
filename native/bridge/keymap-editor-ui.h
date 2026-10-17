/* Original editor chrome. All positions are logical content coordinates;
 * the panel overlays the canvas, never changes the saved mapping space. */
static void keymap_hints_paint(uint32_t *p,int w,int h,
                                const struct keymap_overlay *f) {
  for(size_t i=0;i<(size_t)w*h;++i)p[i]=0;
  if(f->hints_hidden)return;
  for(int i=0;i<f->model.count;++i) {
    const struct keymap_point *q=&f->model.points[i];
    int cx=(int)(((int64_t)q->nx*(w-1)+32767)/65535);
    int cy=(int)(((int64_t)q->ny*(h-1)+32767)/65535);
    int active=!f->disabled&&q->key>0&&q->key<768&&
      ((f->active_keys[q->key/32]&(1u<<(q->key%32)))||f->flash_ticks[q->key]);
    for(int y=-28;y<=28;++y)for(int x=-28;x<=28;++x) {
      int px=cx+x,py=cy+y,d=x*x+y*y;
      if(px<0||px>=w||py<0||py>=h||d>28*28)continue;
      p[(size_t)py*w+px]=d>=25*25?
        (active?0xff8de8ff:0x70355c70):(active?0xb018628a:0x1806080c);
    }
    char key[20];const char *label=keymap_label(q->key,key,sizeof(key));
    int scale=keymap_text_width(label,2)>44?1:2;
    keymap_text_scaled(p,w,h,cx-keymap_text_width(label,scale)/2,cy-7,
                       label,active?0xfff2fbff:0x90909090,scale);
  }
}
struct keymap_rect {
  int x, y, w, h;
};
struct keymap_layout {
  int compact;
  struct keymap_rect panel, add, save, remove, tap, hold, cancel, toggle;
  struct keymap_rect playback, visibility;
};
static int keymap_contains(struct keymap_rect r, int x, int y) {
  return r.w > 0 && r.h > 0 && x >= r.x && y >= r.y && x < r.x + r.w &&
         y < r.y + r.h;
}
static struct keymap_layout keymap_layout(int w, int h, int hidden) {
  struct keymap_layout l = {0};
  l.compact = w < 600 || h < 420;
  if (hidden) {
    l.toggle = (struct keymap_rect){w - 64, 12, 52, 32};
    return l;
  }
  l.playback=(struct keymap_rect){12,68,132,28};
  l.visibility=(struct keymap_rect){152,68,132,28};
  if(h<160)l.playback.h=l.visibility.h=0;
  if (l.compact) {
    l.panel = (struct keymap_rect){8, h - 52, w - 16, 44};
    int unit = (w - 28) / 5, x = 14, y = h - 46;
    l.add = (struct keymap_rect){x, y, unit - 4, 32};
    l.tap = (struct keymap_rect){x + unit, y, unit - 4, 32};
    l.remove = (struct keymap_rect){x + unit * 2, y, unit - 4, 32};
    l.save = (struct keymap_rect){x + unit * 3, y, unit - 4, 32};
    l.cancel = (struct keymap_rect){x + unit * 4, y, unit - 4, 32};
    l.toggle = (struct keymap_rect){w - 64, 12, 52, 32};
    return l;
  }
  int x = w - 252;
  l.panel = (struct keymap_rect){x, 12, 240, h - 24};
  l.cancel = (struct keymap_rect){w - 44, 16, 28, 28};
  l.toggle = (struct keymap_rect){x - 64, 12, 52, 32};
  l.add = (struct keymap_rect){x + 16, 126, 208, 44};
  l.tap = (struct keymap_rect){x + 16, 214, 100, 34};
  l.hold = (struct keymap_rect){x + 124, 214, 100, 34};
  l.remove = (struct keymap_rect){x + 16, 266, 208, 34};
  l.save = (struct keymap_rect){x + 16, h - 68, 208, 40};
  return l;
}
static int keymap_ui_hit(struct keymap_layout l, int x, int y) {
  if (keymap_contains(l.playback,x,y))return 9;
  if (keymap_contains(l.visibility,x,y))return 10;
  if (keymap_contains(l.toggle, x, y))
    return 6;
  if (keymap_contains(l.cancel, x, y))
    return 5;
  if (keymap_contains(l.save, x, y))
    return 2;
  if (keymap_contains(l.add, x, y))
    return 1;
  if (keymap_contains(l.remove, x, y))
    return 3;
  if (keymap_contains(l.tap, x, y))
    return l.compact ? 4 : 7;
  if (keymap_contains(l.hold, x, y))
    return 8;
  return keymap_contains(l.panel, x, y) ? -1 : 0;
}
static void keymap_box(uint32_t *p, int w, int h, struct keymap_rect r,
                       int radius, uint32_t color) {
  for (int y = 0; y < r.h; ++y)
    for (int x = 0; x < r.w; ++x) {
      int dx = x < radius          ? radius - x
               : x >= r.w - radius ? x - (r.w - radius - 1)
                                   : 0;
      int dy = y < radius          ? radius - y
               : y >= r.h - radius ? y - (r.h - radius - 1)
                                   : 0;
      if (dx * dx + dy * dy > radius * radius)
        continue;
      int px = r.x + x, py = r.y + y;
      if (px >= 0 && px < w && py >= 0 && py < h)
        p[(size_t)py * w + px] = color;
    }
}
static void keymap_ui_button(uint32_t *p, int w, int h, struct keymap_rect r,
                             const char *label, int active, int enabled) {
  if (r.w <= 0)
    return;
  keymap_box(p, w, h, r, 6, active ? 0xff167cbb : 0xff293748);
  int scale=keymap_text_width(label,2)+12<=r.w?2:1;
  int size=keymap_text_size(scale);
  keymap_text_scaled_clipped(p,w,h,r.x+r.w-6,
                     r.x+(r.w-keymap_text_width(label,scale))/2,
                     r.y+(r.h-(size==11?8:11))/2,label,
                     enabled?0xffedf6ff:0xff8191a4,scale);
}
static int keymap_point_hit(const struct keymap_model *m, int w, int h, int x,
                            int y) {
  for (int i = m->count - 1; i >= 0; --i) {
    int cx = (int)(((int64_t)m->points[i].nx * (w - 1) + 32767) / 65535);
    int cy = (int)(((int64_t)m->points[i].ny * (h - 1) + 32767) / 65535);
    if (keymap_contains((struct keymap_rect){cx + 17, cy - 33, 18, 18}, x, y))
      return 100 + i;
    int dx = x - cx, dy = y - cy;
    if (dx < -28 || dx > 28 || dy < -28 || dy > 28)
      continue;
    if (dx * dx + dy * dy <= 28 * 28)
      return 200 + i;
  }
  return 0;
}
static void keymap_editor_paint(uint32_t *p, int w, int h,
                                const struct keymap_overlay *f) {
  const struct keymap_model *m = &f->model;
  struct keymap_layout l = keymap_layout(w, h, f->panel_hidden);
  int selected = m->selected >= 0 && m->selected < m->count;
  for (int i = 0; i < m->count; ++i) {
    const struct keymap_point *q = &m->points[i];
    int cx = (int)(((int64_t)q->nx * (w - 1) + 32767) / 65535);
    int cy = (int)(((int64_t)q->ny * (h - 1) + 32767) / 65535);
    int chosen = i == m->selected;
    for (int y = -31; y <= 31; ++y)
      for (int x = -31; x <= 31; ++x) {
        int d = x * x + y * y, px = cx + x, py = cy + y;
        if (d > 31 * 31 || px < 0 || px >= w || py < 0 || py >= h)
          continue;
        uint32_t color = 0;
        if (d <= 25 * 25)
          color = chosen ? 0xdd163751 : 0xc0142233;
        else if (d <= 28 * 28)
          color = chosen ? 0xffa8edff : 0xff32b8f5;
        else if (chosen)
          color = 0x60305a60;
        if (color)
          p[(size_t)py * w + px] = color;
      }
    char key[20];
    const char *label = keymap_label(q->key, key, sizeof(key));
    int scale = keymap_text_width(label,2)>44?1:2;
    keymap_text_scaled(p, w, h, cx-keymap_text_width(label,scale)/2, cy - 7,
                       label, 0xfff3faff, scale);
    if (q->hold || q->kind)
      keymap_text_scaled(p, w, h, cx-keymap_text_width(q->kind?keymap_kind_name(q->kind):"Hold",1)/2, cy + 12, q->kind?keymap_kind_name(q->kind):"Hold", 0xff8cdbff, 1);
    keymap_box(p, w, h, (struct keymap_rect){cx + 17, cy - 33, 18, 18}, 4,
               0xff243b50);
    keymap_text_scaled(p, w, h, cx+26-keymap_text_width("X",1)/2, cy-28, "X", 0xff8bd8ff, 1);
  }
  const char *hint = f->save_pending ? "Saving..."
                     : m->error == 1 ? "Key already used"
                     : m->error == 2 ? "Check keys and WASD"
                     : m->error == 3 ? "Save failed"
                     : m->placing    ? "Click to place a key"
                     : selected      ? "Key / F6 type / F7 end"
                                     : "Select or add a key";
  if (!f->panel_hidden) {
    keymap_box(p, w, h,
               (struct keymap_rect){12, 12, w < 360 ? w - 88 : 236, 48}, 8,
               0xee172331);
    char title[64];
    if(selected&&m->points[m->selected].kind==KEYMAP_MACRO)
      snprintf(title,sizeof(title),"Macro %dms x%d F8/F9",m->points[m->selected].duration,m->points[m->selected].repeats);
    else if(selected&&(m->points[m->selected].kind==KEYMAP_SWIPE||m->points[m->selected].kind==KEYMAP_REPEAT))
      snprintf(title,sizeof(title),"%s %dms F8 interval",keymap_kind_name(m->points[m->selected].kind),m->points[m->selected].duration);
    else if(selected&&m->points[m->selected].kind)
      snprintf(title,sizeof(title),"%s F6 type / F7 target",keymap_kind_name(m->points[m->selected].kind));
    else snprintf(title,sizeof(title),"Keyboard mapping");
    keymap_text_scaled(p, w, h, 24, 22, title, 0xffecf6ff, 1);
    keymap_text_scaled(p, w, h, 24, 40, hint,
                       m->error ? 0xffffad99 : 0xff89d5ff, 1);
  }
  if (!f->panel_hidden) {
    keymap_ui_button(p,w,h,l.playback,f->disabled?"F3: Keymap off":"F3: Keymap on",!f->disabled,1);
    keymap_ui_button(p,w,h,l.visibility,f->hints_hidden?"F4: Markers off":"F4: Markers on",!f->hints_hidden,1);
    keymap_box(p, w, h, l.panel, 10, 0xfa19232f);
    if (!l.compact) {
      int x = l.panel.x;
      keymap_text(p, w, h, x + 16, 28, "Controls", 0xffedf6ff);
      keymap_text_scaled(p, w, h, x + 16, 62, "App profile", 0xff8194aa, 1);
      char pkg[34];
      snprintf(pkg, sizeof(pkg), "%.32s", f->package);
      keymap_text_scaled_clipped(p, w, h, x+224, x + 16, 80, pkg, 0xffbecddd, 1);
      keymap_text_scaled(p, w, h, x + 16, 105, "Place on the app", 0xff8194aa,
                         1);
      keymap_text_scaled(p, w, h, x + 16, 190, "Press behavior", 0xff8194aa, 1);
      if (h >= 470) {
        keymap_text_scaled(p, w, h, x + 16, 322, "F8 interval / F9 count",
                           0xff9cadc0, 1);
        keymap_text_scaled(p, w, h, x + 16, 339, "F6 type / F7 endpoint",
                           0xff9cadc0, 1);
      }
      if (h >= 490) {
        keymap_text_scaled(p, w, h, x + 16, h - 124, "F3 / F4 apply now", 0xff8194aa,
                           1);
        keymap_text_scaled(p, w, h, x + 16, h - 108,
                           "For this app window", 0xff8194aa, 1);
      }
      char count[48];
      if(selected&&m->points[m->selected].kind)
        snprintf(count,sizeof(count),"%s",keymap_kind_name(m->points[m->selected].kind));
      else snprintf(count, sizeof(count), "%d of 64 controls", m->count);
      keymap_text_scaled(p, w, h, x + 16, h - 88, count, 0xff9cadc0, 1);
    }
    int hold = selected ? m->points[m->selected].hold : f->placement_hold;
    keymap_ui_button(p, w, h, l.add, l.compact ? "Add" : "Add key", m->placing,
                     m->count < 64);
    keymap_ui_button(p, w, h, l.tap,
                     l.compact ? (hold ? "Hold" : "Tap") : "Tap", !hold, 1);
    keymap_ui_button(p, w, h, l.hold, "Hold", hold, 1);
    keymap_ui_button(p, w, h, l.remove, l.compact ? "Del" : "Delete key", 0,
                     selected);
    keymap_ui_button(p, w, h, l.save, f->save_pending ? "Wait" : "Save", 1,
                     !f->save_pending);
    keymap_ui_button(p, w, h, l.cancel, l.compact ? "Exit" : "X", 0, 1);
  }
  keymap_ui_button(p, w, h, l.toggle, f->panel_hidden ? "Show" : "Hide", 0, 1);
}
