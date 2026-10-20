#define ANVIL_KEYMAP_EDITOR 1
#include "../native/bridge/bridge.c"
#include <assert.h>
int __android_log_print(int l,const char *t,const char *f,...) {
  (void)l;(void)t;(void)f;return 0;
}
static void click(struct object *p,int x,int y) {
  p->csd_x=x;p->csd_y=y;
  keymap_overlay_button(p,272,1);keymap_overlay_button(p,272,0);
}
static void test_delete_capture(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700}};
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
  assert(keymap_add(&f.model,250,250,1000,700));
  assert(keymap_assign(&f.model,44));
  struct keymap_layout l=keymap_layout(1000,700,0);
  click(&pointer,l.properties[0].x+2,l.properties[0].y+2);
  assert(f.binding_capture==1);
  click(&pointer,275,225); /* Selected marker's delete badge. */
  assert(!f.model.count&&f.model.selected==-1);
  assert(!f.binding_capture&&!f.property_page&&!f.dragging);
  free(f.history);
}
static void test_geometry_cancel(void) {
  for(int origin=0;origin<2;++origin) {
    struct keymap_overlay f={.piece={.width=1000,.height=700},.sync_valid=1};
    struct object owner={.overlay=&f},pointer={.overlay_focus=&owner};
    owner.next=&pointer;objects=&owner;
    assert(keymap_add(&f.model,250,250,1000,700));
    assert(keymap_assign(&f.model,44));
    f.model.dirty=0;
    struct keymap_layout l=keymap_layout(1000,700,0);
    pointer.csd_x=l.save.x+2;pointer.csd_y=l.save.y+2;
    keymap_overlay_button(&pointer,272,1);
    assert(pointer.overlay_pressed==2);
    f.dragging=1;
    keymap_overlay_sync(&owner,origin?10:0,0,origin?1000:900,700);
    assert(!pointer.overlay_pressed&&!f.dragging);
    /* The old press cannot Save when released over the relocated toolbar. */
    keymap_overlay_button(&pointer,272,0);
    assert(!f.save_pending&&!f.model.dirty);
    free(f.history);
  }
}
static void test_stale_selection(void) {
  struct keymap_overlay f={.piece={.width=1000,.height=700},.dragging=1};
  f.model.selected=-1;
  struct object owner={.overlay=&f},pointer={.overlay_focus=&owner,.csd_x=250,.csd_y=250};
  keymap_overlay_motion(&pointer);
  assert(!f.dragging&&!f.model.dirty);
}
static void test_conflict_feedback(void) {
  struct keymap_overlay f={.piece={.width=448,.height=700}};
  assert(keymap_add(&f.model,100,300,448,700));
  keymap_set_kind(&f.model.points[0],KEYMAP_DPAD);
  assert(keymap_add(&f.model,200,400,448,700));
  assert(keymap_assign(&f.model,44));
  struct keymap_point before=f.model.points[1];
  assert(!keymap_assign(&f.model,30)&&f.model.error==1);
  assert(!memcmp(&before,&f.model.points[1],sizeof(before)));
  uint32_t *pixels=calloc(448*700,4);assert(pixels);
  keymap_editor_paint(pixels,448,700,&f);
  int error_pixels=0;
  for(int y=658;y<688;++y)for(int x=12;x<156;++x)
    if(((pixels[y*448+x]>>16)&255)>((pixels[y*448+x]>>8)&255)+30)++error_pixels;
  assert(error_pixels>20);free(pixels);
  assert(keymap_ui_hit(keymap_layout(448,700,0),30,670)==-1);
  assert(!keymap_assign(&f.model,60)&&f.model.error==2);
  assert(!memcmp(&before,&f.model.points[1],sizeof(before)));
  assert(keymap_assign(&f.model,45)&&!f.model.error&&keymap_valid(&f.model,1));
  free(f.history);
}
static void test_task_replacement(void) {
  struct object owner={.app=1,.app_id="org.anvildroid.touchprobe",.has_bounds=1};
  objects=&owner;task_count=1;tasks[0].id=42;
  snprintf(tasks[0].package,sizeof(tasks[0].package),"%s",owner.app_id);
  owner.overlay=calloc(1,sizeof(*owner.overlay));assert(owner.overlay);
  owner.overlay->task=41;owner.overlay->token=1;owner.overlay->save_pending=1;
  keymap_overlay_sync(&owner,0,0,1000,700);
  assert(!owner.overlay&&owner.overlay_dismissed);
  task_count=0;
}
int main(void) {
  test_delete_capture();test_stale_selection();test_task_replacement();test_geometry_cancel();test_conflict_feedback();
  objects=0;
  const char message[]="Keymap editor lifecycle: PASS\n";
  assert(write(1,message,sizeof(message)-1)==sizeof(message)-1);
}
