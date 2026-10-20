#define ANVIL_KEYMAP_EDITOR 1
#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int l,const char *t,const char *f,...) {(void)l;(void)t;(void)f;return 0;}
static void click(struct object *p,struct keymap_rect r) {
  assert(r.w>0&&r.h>0);p->csd_x=r.x+r.w/2;p->csd_y=r.y+r.h/2;
  keymap_overlay_button(p,272,1);keymap_overlay_button(p,272,0);
}
static void at(struct object *p,int x,int y) {click(p,(struct keymap_rect){x,y,1,1});}
static void choose_type(struct object *p,struct keymap_overlay *f,int kind) {
  if(f->panel_hidden)click(p,keymap_layout(f->piece.width,f->piece.height,1).toggle);
  if(!f->type_open)click(p,keymap_layout(f->piece.width,f->piece.height,0).add);
  while(!keymap_type_rect(f,kind).w)keymap_editor_scroll(f,1);
  click(p,keymap_type_rect(f,kind));
  assert(!f->type_open&&f->placement_kind==kind&&f->model.placing);
}
static void field(struct object *p,struct keymap_overlay *f,int id,int side) {
  int fields[32],n=keymap_inspector_fields(f,fields),index=-1;
  for(int i=0;i<n;++i)if(fields[i]==id)index=i;
  assert(index>=0);f->inspector_scroll=index;
  struct keymap_rect r=keymap_layout(f->piece.width,f->piece.height,0).properties[0];
  p->csd_x=side<0?r.x+4:side>0?r.x+r.w-4:r.x+r.w/2;p->csd_y=r.y+r.h/2;
  keymap_overlay_button(p,272,1);keymap_overlay_button(p,272,0);
}
static void test_ui(int w,int h) {
  struct keymap_overlay f={.piece={.width=w,.height=h}};f.model.selected=-1;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  struct keymap_layout l=keymap_layout(w,h,0);
  assert(keymap_ui_hit(l,l.save.x+2,l.save.y+2)==2);
  choose_type(&pointer,&f,KEYMAP_BASIC);
  click(&pointer,l.toggle);assert(f.panel_hidden);
  at(&pointer,w/2,h/2);assert(f.model.count==1&&f.binding_capture);
  assert(!f.model.points[0].key); /* Placement click never becomes Mouse1. */
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,57));f.binding_capture=0;
  int nx=f.model.points[0].nx,ny=f.model.points[0].ny;
  click(&pointer,keymap_layout(w,h,1).toggle);assert(!f.panel_hidden);
  assert(f.model.points[0].nx==nx&&f.model.points[0].ny==ny);
  click(&pointer,l.save);assert(f.save_pending);f.save_pending=0;
  size_t n=(size_t)w*h;uint32_t *pixels=calloc(n+2,4);assert(pixels);
  pixels[0]=0x12345678;pixels[n+1]=0x87654321;
  for(int kind=0;kind<keymap_palette_count();++kind){
    f.type_open=1;f.palette_scroll=kind;
    struct keymap_rect r=keymap_type_rect(&f,kind);
    assert(r.x>=0&&r.y>=0&&r.x+r.w<=w&&r.y+r.h<=h);
    assert(keymap_type_hit(&f,r.x+2,r.y+2)==40+kind);
    keymap_editor_paint(pixels+1,w,h,&f);
  }
  f.type_open=0;keymap_editor_paint(pixels+1,w,h,&f);
  assert(pixels[0]==0x12345678&&pixels[n+1]==0x87654321);
  for(size_t i=1;i<=n;++i){unsigned a=pixels[i]>>24;assert(((pixels[i]>>16)&255)<=a&&((pixels[i]>>8)&255)<=a&&(pixels[i]&255)<=a);}
  free(pixels);free(f.history);
}
static void test_authoring(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700},.baseline_valid=1};f.model.selected=-1;f.baseline=f.model;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  struct keymap_layout l=keymap_layout(1000,700,0);
  choose_type(&pointer,&f,KEYMAP_BASIC);at(&pointer,250,250);
  assert(f.model.count==1&&f.binding_capture&&f.model.points[0].key==0);
  keymap_overlay_button(&pointer,273,1);keymap_overlay_button(&pointer,273,0);
  assert(!f.binding_capture&&f.model.points[0].source==KEYMAP_SOURCE_MOUSE&&f.model.points[0].key==273);
  struct keymap_point *q=&f.model.points[0];assert(q->duration==50&&q->end_mode==KEYMAP_END_RELEASE);
  /* Click the label, then bind Space; the opening click cannot bind Mouse1. */
  at(&pointer,250,250);assert(f.binding_capture&&q->key==273);
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,57));f.binding_capture=0;
  /* The same label supports dragging when movement exceeds click slop. */
  pointer.csd_x=250;pointer.csd_y=250;keymap_overlay_button(&pointer,272,1);
  pointer.csd_x=300;pointer.csd_y=280;keymap_overlay_motion(&pointer);keymap_overlay_button(&pointer,272,0);
  assert(q->nx==keymap_normalize(300,1000)&&q->ny==keymap_normalize(280,700)&&!f.binding_capture);
  click(&pointer,l.undo);assert(q->nx==keymap_normalize(250,1000));
  click(&pointer,l.redo);assert(q->nx==keymap_normalize(300,1000));
  field(&pointer,&f,KP_END,0);assert(keymap_effective_hold(q));
  click(&pointer,l.clone);assert(f.model.count==2&&f.binding_capture&&!f.model.points[1].key);
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,19));f.binding_capture=0;
  click(&pointer,l.test);assert(f.testing&&!f.save_pending);
  assert(!keymap_overlay_axis(&pointer,0,256));
  click(&pointer,(struct keymap_rect){12,12,184,30});assert(!f.testing&&!f.close_pending&&!f.save_pending);
  /* Choosing another tool preserves the current control and arms a placement. */
  struct keymap_point before=f.model.points[1];choose_type(&pointer,&f,KEYMAP_SWIPE);
  assert(!memcmp(&before,&f.model.points[1],sizeof(before)));at(&pointer,420,400);
  assert(f.model.count==3&&f.model.points[2].kind==KEYMAP_SWIPE&&f.binding_capture);
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,44));f.binding_capture=0;
  field(&pointer,&f,KP_DURATION,1);assert(f.model.points[2].duration==300);
  field(&pointer,&f,KP_DURATION,0);assert(f.numeric_capture);
  f.numeric_value=750;keymap_numeric_apply(&f);assert(!f.numeric_capture&&f.model.points[2].duration==750);
  click(&pointer,l.cancel);assert(f.close_pending&&f.ack_pending==3&&!f.save_pending);
  free(f.history);
  /* FPS starter is two existing MIT controls, never a hidden new profile type. */
  f=(struct keymap_overlay){.piece={.width=1000,.height=700}};f.model.selected=-1;
  choose_type(&pointer,&f,10);at(&pointer,400,300);
  assert(f.model.count==2&&f.model.points[0].kind==KEYMAP_LOOK&&f.model.points[0].activation);
  assert(f.model.points[1].kind==KEYMAP_BASIC&&f.model.points[1].source==KEYMAP_SOURCE_MOUSE&&f.model.points[1].key==272&&keymap_effective_hold(&f.model.points[1]));
  assert(keymap_valid(&f.model,1));free(f.history);
}
static void test_capture_then_drag(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700}};f.model.selected=-1;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  assert(keymap_add(&f.model,250,250,1000,700));keymap_set_kind(&f.model.points[0],KEYMAP_BASIC);
  assert(keymap_assign(&f.model,44));
  assert(keymap_add(&f.model,500,400,1000,700));keymap_set_kind(&f.model.points[1],KEYMAP_BASIC);
  assert(keymap_assign_source(&f.model,KEYMAP_SOURCE_MOUSE,272));
  struct keymap_point *q=&f.model.points[0];f.model.selected=0;
  for(int ring=0;ring<2;++ring){
    int cx=(int)((int64_t)q->nx*999/65535),cy=(int)((int64_t)q->ny*699/65535);
    at(&pointer,cx,cy);assert(f.binding_capture&&!f.model.error);
    pointer.csd_x=cx+(ring?25:0);pointer.csd_y=cy;
    keymap_overlay_button(&pointer,272,1);
    assert(q->source==KEYMAP_SOURCE_KEY&&q->key==44&&!f.model.error&&f.deferred_binding);
    pointer.csd_x=cx+50;pointer.csd_y=cy+30;keymap_overlay_motion(&pointer);
    assert(!f.binding_capture&&!f.model.error);
    keymap_overlay_button(&pointer,272,0);
    assert(q->nx==keymap_normalize(cx+50,1000)&&q->ny==keymap_normalize(cy+30,700));
    assert(q->source==KEYMAP_SOURCE_KEY&&q->key==44&&keymap_valid(&f.model,1));
  }
  int cx=(int)((int64_t)q->nx*999/65535),cy=(int)((int64_t)q->ny*699/65535);
  at(&pointer,cx,cy);assert(f.binding_capture);
  /* With capture armed, a true click still binds M1, and checks conflicts
   * on release rather than turning a possible drag into a failed binding. */
  keymap_overlay_button(&pointer,272,1);assert(!f.model.error&&q->key==44);
  keymap_overlay_button(&pointer,272,0);assert(f.model.error==1&&f.binding_capture&&q->key==44);
  f.model.selected=1;keymap_remove(&f.model);f.model.selected=0;f.model.error=0;
  keymap_overlay_button(&pointer,272,1);assert(q->key==44);
  keymap_overlay_button(&pointer,272,0);assert(!f.binding_capture&&q->source==KEYMAP_SOURCE_MOUSE&&q->key==272);
  free(f.history);
}
static void test_availability_affordance(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700}};f.model.selected=-1;
  assert(keymap_add(&f.model,250,250,1000,700));
  struct keymap_point *q=&f.model.points[0];keymap_set_kind(q,KEYMAP_LOOK);q->key=19;
  assert(!keymap_editor_can_clone(&f));
  int fields[32],count=keymap_inspector_fields(&f,fields),row=-1;
  for(int i=0;i<count;++i)if(fields[i]==KP_CAPTURE)row=i;
  assert(row>=0);char text[100];
  const char *states[]={"No API","Ready","Pending","Active"};
  for(int state=0;state<4;++state){
    f.capture_status=state;
    assert(strstr(keymap_property_label(&f,row,text,sizeof(text)),states[state]));
    assert(keymap_property_enabled(&f,row)==(state!=0));
  }
  f.capture_status=0;keymap_property_action(&f,row,0);assert(!q->activation);
  f.capture_status=1;keymap_property_action(&f,row,0);assert(q->activation);
  f.capture_status=0;assert(keymap_property_enabled(&f,row)); /* Can leave saved capture mode. */
  keymap_property_action(&f,row,0);assert(q->activation==KEYMAP_CAMERA_AUTO);
  assert(strstr(keymap_property_label(&f,row,text,sizeof(text)),"automatic"));
  keymap_property_action(&f,row,0);assert(!q->activation&&!keymap_property_enabled(&f,row));
  int singletons[]={KEYMAP_LOOK,KEYMAP_MOUSE_TOUCH,KEYMAP_WALK};
  for(unsigned i=0;i<sizeof(singletons)/sizeof(singletons[0]);++i){
    keymap_set_kind(q,singletons[i]);assert(!keymap_editor_can_clone(&f));
    keymap_editor_clone(&f);assert(f.model.count==1);
  }
  keymap_set_kind(q,KEYMAP_BASIC);assert(keymap_editor_can_clone(&f));
  free(f.history);
}
static void test_automatic_camera_authoring(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700},.capture_status=1};f.model.selected=-1;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  choose_type(&pointer,&f,KEYMAP_LOOK);at(&pointer,400,350);
  struct keymap_point *q=&f.model.points[0];
  assert(q->kind==KEYMAP_LOOK&&q->activation==KEYMAP_CAMERA_AUTO&&q->key==63&&q->suspend_key==56);
  assert(!f.binding_capture&&keymap_valid(&f.model,1));
  char label[100];assert(!strcmp(keymap_point_label(q,label,sizeof(label)),"F5"));
  field(&pointer,&f,KP_BIND,0);assert(f.binding_capture);
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,87));f.binding_capture=0;
  field(&pointer,&f,KP_CAPTURE,0);assert(q->activation==KEYMAP_CAMERA_HOLD&&!f.binding_capture);
  field(&pointer,&f,KP_CAPTURE,0);assert(q->activation==KEYMAP_CAMERA_TOGGLE);
  field(&pointer,&f,KP_CAPTURE,0);assert(q->activation==KEYMAP_CAMERA_AUTO);
  click(&pointer,keymap_layout(1000,700,0).test);assert(f.testing&&!f.save_pending);
  free(f.history);
  f=(struct keymap_overlay){.piece={.width=1000,.height=700}};
  keymap_editor_fps(&f,400,350);
  assert(f.model.count==2&&f.model.points[0].activation==KEYMAP_CAMERA_AUTO);
  assert(f.model.points[0].key==63&&f.model.points[1].key==272&&keymap_valid(&f.model,1));
}
static void test_inspector(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700},.capture_status=1};f.model.selected=-1;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  choose_type(&pointer,&f,KEYMAP_DPAD);at(&pointer,250,250);
  struct keymap_point *q=&f.model.points[0];assert(q->key==17&&!f.binding_capture);
  field(&pointer,&f,KP_DIRECTIONS,0);assert(q->up_key==103&&q->left_key==105&&q->key==103);
  field(&pointer,&f,KP_UP,0);assert(f.binding_capture&&f.capture_field==KP_UP);
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,16));f.binding_capture=0;assert(q->key==16);
  field(&pointer,&f,KP_WALK_KEY,0);assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,42));f.binding_capture=0;
  field(&pointer,&f,KP_WALK_RADIUS,0);f.numeric_value=35;keymap_numeric_apply(&f);assert(q->walk_radius==35);
  field(&pointer,&f,KP_RAMP,-1);assert(q->ramp_ms==60);
  field(&pointer,&f,KP_WALK_KEY,0);keymap_editor_clear_optional(&f);assert(!q->walk_key&&!q->walk_radius&&keymap_valid(&f.model,1));
  choose_type(&pointer,&f,KEYMAP_SKILL);at(&pointer,400,400);q=&f.model.points[1];
  assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,18));f.binding_capture=0;
  field(&pointer,&f,KP_SKILL_MODE,0);assert(q->skill_mode==1);
  field(&pointer,&f,KP_CANCEL_KEY,0);assert(keymap_editor_assign(&f,KEYMAP_SOURCE_KEY,19));f.binding_capture=0;
  field(&pointer,&f,KP_CANCEL_X,0);f.numeric_value=80;keymap_numeric_apply(&f);assert(q->cancel_x>52000);
  field(&pointer,&f,KP_CANCEL_KEY,0);keymap_editor_clear_optional(&f);assert(!q->cancel_key&&!q->cancel_x&&keymap_valid(&f.model,1));
  /* Wheel and small-window scrolling expose fields without cycling pages. */
  f.piece.width=300;f.piece.height=120;f.inspector_scroll=0;
  assert(keymap_overlay_axis(&pointer,0,256)&&f.inspector_scroll==1);
  assert(keymap_overlay_axis(&pointer,0,-256)&&f.inspector_scroll==0);
  free(f.history);
}
int main(int argc, char **argv) {
  test_authoring();
  test_inspector();
  test_capture_then_drag();
  test_availability_affordance();
  test_automatic_camera_authoring();
  test_ui(1200, 700);
  test_ui(800, 600);
  test_ui(600, 420);
  test_ui(500, 350);
  test_ui(300, 120);
  if (argc > 1 && (!strcmp(argv[1], "--render") || !strcmp(argv[1], "--hints") || !strcmp(argv[1], "--palette") || !strcmp(argv[1], "--fps") || !strcmp(argv[1], "--skill") || !strcmp(argv[1], "--movement"))) {
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
    if(!strcmp(argv[1],"--palette"))f.type_open=1;
    if(!strcmp(argv[1],"--fps")){f.model=(struct keymap_model){.selected=-1};keymap_editor_fps(&f,w/2,h/2);}
    if(!strcmp(argv[1],"--skill")||!strcmp(argv[1],"--movement")){
      f.model=(struct keymap_model){.selected=-1};keymap_add(&f.model,w/2,h/2,w,h);
      struct keymap_point *q=&f.model.points[0];keymap_set_kind(q,!strcmp(argv[1],"--skill")?KEYMAP_SKILL:KEYMAP_DPAD);
      if(q->kind==KEYMAP_SKILL){q->key=16;q->cancel_key=19;q->cancel_x=50000;q->cancel_y=12000;}
    }
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
