#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int l, const char *t, const char *f, ...) {
  (void)l;
  (void)t;
  (void)f;
  return 0;
}
static void click(struct object *pointer, struct keymap_rect r) {
  pointer->csd_x = r.x + r.w / 2;
  pointer->csd_y = r.y + r.h / 2;
  keymap_overlay_button(pointer, 0x110, 1);
  keymap_overlay_button(pointer, 0x110, 0);
}
static void choose_type(struct object *pointer,struct keymap_overlay *f,int kind) {
  click(pointer,keymap_layout(f->piece.width,f->piece.height,0).properties[0]);
  assert(f->type_open);
  click(pointer,keymap_type_rect(f,kind));
  assert(!f->type_open&&f->placement_kind==kind);
}
static void test_ui(int w, int h) {
  struct keymap_overlay f = {0};
  f.model.selected = -1;
  f.piece.width = w;
  f.piece.height = h;
  struct object s = {.overlay = &f}, pointer = {.overlay_focus = &s};
  struct keymap_layout l = keymap_layout(w, h, 0);
  if(l.playback.h) {
    click(&pointer,l.playback);assert(s.keymap_disabled&&f.disabled);
    click(&pointer,l.visibility);assert(s.keymap_hidden&&f.hints_hidden);
    click(&pointer,l.playback);assert(!s.keymap_disabled&&s.keymap_hidden);
    click(&pointer,l.visibility);assert(!s.keymap_hidden);
  }
  assert(keymap_ui_hit(l, l.save.x + 2, l.save.y + 2) == 2);
  click(&pointer, l.add);
  assert(f.model.placing);
  int count = f.model.count;
  pointer.csd_x = l.panel.x + 1;
  pointer.csd_y = l.panel.y + 1;
  keymap_overlay_button(&pointer, 0x110, 1);
  keymap_overlay_button(&pointer, 0x110, 0);
  assert(f.model.count == count); /* panel background never places a key */
  click(&pointer, l.toggle);
  assert(f.panel_hidden);
  l = keymap_layout(w, h, 1);
  assert(!l.panel.w);
  pointer.csd_x = w / 2;
  pointer.csd_y = h / 2 + 8;
  keymap_overlay_button(&pointer, 0x110, 1);
  keymap_overlay_button(&pointer, 0x110, 0);
  assert(f.model.count == 1 && !f.model.placing);
  assert(f.binding_capture==1); /* Placing immediately asks for the key. */
  assert(keymap_assign(&f.model, 30));
  int nx = f.model.points[0].nx, ny = f.model.points[0].ny;
  click(&pointer, l.toggle);
  assert(!f.panel_hidden);
  assert(f.model.points[0].nx == nx && f.model.points[0].ny == ny);
  l = keymap_layout(w, h, 0);
  click(&pointer, l.compact ? l.tap : l.hold);
  assert(f.model.points[0].hold);
  click(&pointer, l.save);
  assert(f.save_pending == 1);
  f.save_pending = 0;
  click(&pointer, l.toggle);
  int cx = (int)(((int64_t)nx * (w - 1) + 32767) / 65535);
  int cy = (int)(((int64_t)ny * (h - 1) + 32767) / 65535);
  assert(keymap_point_hit(&f.model, w, h, cx, cy) == 200);
  assert(keymap_point_hit(&f.model, w, h, -8000000, 8000000) == 0);
  int resized_x = (int)(((int64_t)nx * (w * 2 - 1) + 32767) / 65535);
  int resized_y = (int)(((int64_t)ny * (h * 2 - 1) + 32767) / 65535);
  assert(keymap_point_hit(&f.model, w * 2, h * 2, resized_x, resized_y) == 200);
  assert(f.model.points[0].nx == nx && f.model.points[0].ny == ny);
  click(&pointer, (struct keymap_rect){cx + 17, cy - 33, 18, 18});
  assert(f.model.count == 0);
  /* Renderer stays in bounds and uses premultiplied ARGB at all sizes. */
  size_t n = (size_t)w * h;
  uint32_t *p = calloc(n + 2, sizeof(*p));
  assert(p);
  p[0] = 0x12345678;
  p[n + 1] = 0x87654321;
  f.panel_hidden = 0;
  f.type_open=1;
  for(int i=0;i<=KEYMAP_LOOK;++i) {
    struct keymap_rect r=keymap_type_rect(&f,i);
    assert(r.x>=0&&r.y>=0&&r.x+r.w<=w&&r.y+r.h<=h);
    assert(keymap_type_hit(&f,r.x+2,r.y+2)==40+i);
  }
  keymap_editor_paint(p + 1, w, h, &f);
  assert(p[0] == 0x12345678 && p[n + 1] == 0x87654321);
  for (size_t i = 1; i <= n; ++i) {
    unsigned a = p[i] >> 24;
    assert(((p[i] >> 16) & 255) <= a);
    assert(((p[i] >> 8) & 255) <= a);
    assert((p[i] & 255) <= a);
  }
  free(p);
}
static void test_authoring(void) {
  struct keymap_overlay f={0};f.model.selected=-1;f.piece.width=1000;f.piece.height=700;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  struct keymap_layout l=keymap_layout(1000,700,0);
  click(&pointer,l.add);
  pointer.csd_x=250;pointer.csd_y=250;
  keymap_overlay_button(&pointer,272,1);keymap_overlay_button(&pointer,272,0);
  assert(f.model.count==1&&f.model.points[0].key==0);
  assert(f.binding_capture==1);
  assert(f.model.points[0].end_mode==KEYMAP_END_RELEASE&&f.model.points[0].duration==50);
  click(&pointer,l.properties[5]);assert(f.binding_capture==2);
  keymap_overlay_button(&pointer,273,1);keymap_overlay_button(&pointer,273,0);
  assert(!f.binding_capture&&f.model.points[0].source==KEYMAP_SOURCE_MOUSE&&f.model.points[0].key==273);
  char marker[20];assert(!strcmp(keymap_point_label(&f.model.points[0],marker,sizeof(marker)),"M2"));
  choose_type(&pointer,&f,KEYMAP_SWIPE);
  assert(f.model.points[0].source==KEYMAP_SOURCE_MOUSE&&f.model.points[0].key==273);
  click(&pointer,l.hold);assert(f.model.points[0].end_mode==KEYMAP_END_HOLD);
  /* Type selector, radius handle, and page controls all edit persisted fields. */
  choose_type(&pointer,&f,KEYMAP_DPAD);assert(f.model.points[0].kind==KEYMAP_DPAD);
  assert(!strcmp(keymap_point_label(&f.model.points[0],marker,sizeof(marker)),"WASD"));
  struct keymap_point *q=&f.model.points[0];
  int hx=250+(int)((int64_t)q->radius*700/65535);
  pointer.csd_x=hx;pointer.csd_y=250;keymap_overlay_button(&pointer,272,1);
  assert(f.dragging==3);pointer.csd_x+=30;keymap_overlay_motion(&pointer);
  keymap_overlay_button(&pointer,272,0);assert(q->radius>8192);
  choose_type(&pointer,&f,KEYMAP_SWIPE);assert(q->kind==KEYMAP_SWIPE);
  int ex=(int)((int64_t)q->ex*999/65535),ey=(int)((int64_t)q->ey*699/65535);
  pointer.csd_x=ex;pointer.csd_y=ey;keymap_overlay_button(&pointer,272,1);
  assert(f.dragging==2);pointer.csd_y+=40;keymap_overlay_motion(&pointer);keymap_overlay_button(&pointer,272,0);
  assert(q->ey>q->ny);
  click(&pointer,l.properties[2]);assert(f.property_page==1);
  int duration=q->duration;click(&pointer,l.properties[5]);assert(q->duration==duration+50);
  keymap_set_kind(q,KEYMAP_LOOK);
  f.property_page=0;click(&pointer,l.properties[2]);assert(f.property_page==3);
  int extent=q->look_width;click(&pointer,l.properties[4]);assert(q->look_width<extent);
  click(&pointer,l.properties[2]);int sensitivity=q->sensitivity_x;
  click(&pointer,l.properties[5]);assert(q->sensitivity_x==sensitivity+10);
  click(&pointer,l.properties[2]);click(&pointer,l.properties[4]);
  click(&pointer,l.properties[5]);assert(!q->activation); /* unavailable capture is disabled */
  f.capture_status=1;click(&pointer,l.properties[5]);
  assert(q->invert_y&&q->activation&&keymap_valid(&f.model,1));
  keymap_set_kind(q,KEYMAP_MOUSE_TOUCH);click(&pointer,l.properties[4]);assert(!f.model.count);
  /* Hold chosen before placing must author the runtime's explicit end mode. */
  f.property_page=0;f.placement_kind=KEYMAP_BASIC;
  click(&pointer,l.hold);click(&pointer,l.add);
  pointer.csd_x=200;pointer.csd_y=200;keymap_overlay_button(&pointer,272,1);keymap_overlay_button(&pointer,272,0);
  assert(f.model.count==1&&f.model.points[0].end_mode==KEYMAP_END_HOLD&&f.model.dirty);
  q=&f.model.points[0];q->end_mode=KEYMAP_END_LEGACY;q->hold=0;
  assert(keymap_effective_hold(q));
  q->kind=KEYMAP_SWIPE;q->hold=1;assert(!keymap_effective_hold(q));
  char label[40];assert(!strcmp(keymap_property_label(&f,7,label,sizeof(label)),"Release"));
  q->kind=KEYMAP_BASIC;assert(!strcmp(keymap_property_label(&f,7,label,sizeof(label)),"Hold"));
  q->duration=0;q->repeats=0; /* Imported V1 Basic row has no duration. */
  assert(q->end_mode==KEYMAP_END_LEGACY&&keymap_effective_hold(q));
  click(&pointer,l.tap);
  assert(q->end_mode==KEYMAP_END_RELEASE&&q->duration==50&&f.model.dirty);
  /* Picking the current type must preserve timing, source and endpoint. */
  q->duration=350;
  struct keymap_point before=*q;
  choose_type(&pointer,&f,KEYMAP_BASIC);
  assert(!memcmp(q,&before,sizeof(before)));
  /* A click outside the picker dismisses it without activating Save. */
  click(&pointer,l.properties[0]);assert(f.type_open);
  click(&pointer,l.save);assert(!f.type_open&&!f.save_pending);
  click(&pointer,l.cancel);assert(f.close_pending&&f.ack_pending==3&&!f.save_pending&&f.model.dirty);
  /* A new WASD preset converted to Look inherits W; if another joystick
   * owns W, ask for its replacement immediately rather than ignore typing. */
  f=(struct keymap_overlay){.piece={.width=1000,.height=700}};
  keymap_add(&f.model,200,400,1000,700);keymap_set_kind(&f.model.points[0],KEYMAP_DPAD);
  keymap_add(&f.model,600,400,1000,700);keymap_set_kind(&f.model.points[1],KEYMAP_DPAD);
  keymap_editor_choose_kind(&f,KEYMAP_LOOK);
  assert(f.binding_capture==1);
  assert(keymap_assign(&f.model,19)&&keymap_valid(&f.model,1));
}
int main(int argc, char **argv) {
  test_authoring();
  test_ui(1200, 700);
  test_ui(800, 600);
  test_ui(600, 420);
  test_ui(500, 350);
  test_ui(300, 120);
  if (argc > 1 && (!strcmp(argv[1], "--render") || !strcmp(argv[1], "--hints"))) {
    int w = 1200, h = 700;
    if (argc == 4) {
      assert(sscanf(argv[2], "%d", &w) == 1);
      assert(sscanf(argv[3], "%d", &h) == 1);
    }
    assert(w >= 300 && w <= 2000 && h >= 120 && h <= 1500);
    uint32_t *p = calloc((size_t)w * h, sizeof(*p));
    assert(p);
    /* Neutral diagnostic canvas, not a screenshot of a running Android app. */
    for (int y = 0; y < h; ++y)
      for (int x = 0; x < w; ++x)
        p[(size_t)y * w + x] =
            0xff000000u | ((unsigned)(18 + y * 14 / h) << 16) |
            ((unsigned)(27 + x * 12 / w) << 8) | (unsigned)(38 + y * 15 / h);
    struct keymap_overlay f = {0};
    f.model.selected = -1;
    f.piece.width=w;f.piece.height=h;
    snprintf(f.package, sizeof(f.package), "com.shg.sg432");
    int keys[] = {17, 30, 31, 32, 57, 19};
    int xs[] = {180, 120, 180, 240, 650, 770};
    int ys[] = {410, 470, 470, 470, 480, 360};
    for (int i = 0; i < 6; ++i) {
      keymap_add(&f.model, xs[i] * w / 1200, ys[i] * h / 700, w, h);
      keymap_assign(&f.model, keys[i]);
    }
    f.model.selected = 4;
    f.model.points[4].hold = 1;
    if(!strcmp(argv[1],"--hints")) {
      f.passive=1;f.active_keys[17/32]=1u<<(17%32);
      keymap_hints_paint(p,w,h,&f);
      for(size_t i=0;i<(size_t)w*h;++i) {
        unsigned a=255-(p[i]>>24);
        unsigned r=((p[i]>>16)&255)+24*a/255;
        unsigned g=((p[i]>>8)&255)+32*a/255;
        unsigned b=(p[i]&255)+44*a/255;
        p[i]=0xff000000u|(r<<16)|(g<<8)|b;
      }
    } else keymap_editor_paint(p, w, h, &f);
    char header[64];
    int len = snprintf(header, sizeof(header), "P6\n%d %d\n255\n", w, h);
    assert(write(1, header, (unsigned long)len) == len);
    unsigned char *row = calloc((size_t)w, 3);
    assert(row);
    for (int y = 0; y < h; ++y) {
      for (int x = 0; x < w; ++x) {
        uint32_t c = p[(size_t)y * w + x];
        row[x * 3] = c >> 16;
        row[x * 3 + 1] = c >> 8;
        row[x * 3 + 2] = c;
      }
      assert(write(1, row, (unsigned long)w * 3) == w * 3);
    }
    free(row);
    free(p);
  }
}
