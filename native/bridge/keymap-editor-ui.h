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
    char key[20];const char *label=keymap_point_label(q,key,sizeof(key));
    int scale=keymap_text_width(label,2)>44?1:2;
    keymap_text_scaled(p,w,h,cx-keymap_text_width(label,scale)/2,cy-7,
                       label,active?0xfff2fbff:0x90909090,scale);
  }
}
struct keymap_rect { int x,y,w,h; };
struct keymap_layout {
  int compact, rows;
  struct keymap_rect panel,add,save,remove,tap,hold,cancel,toggle,status;
  struct keymap_rect playback,visibility,undo,redo,clone,test,up,down,detach;
  struct keymap_rect properties[16];
};
static int keymap_contains(struct keymap_rect r,int x,int y) {
  return r.w>0&&r.h>0&&x>=r.x&&y>=r.y&&x<r.x+r.w&&y<r.y+r.h;
}
static struct keymap_layout keymap_layout(int w,int h,int hidden) {
  struct keymap_layout l={0};l.compact=w<600||h<420;
  l.toggle=(struct keymap_rect){12,12,60,26};
  if(hidden)return l;
  int pw=w-16;if(pw>276)pw=276;
  int x=w-pw-8,y=h<200?12:14,rh=h<200?22:26,step=rh+4;
  l.panel=(struct keymap_rect){x,8,pw,h-16};
  int unit=(pw-12)/4;
  struct keymap_rect *tools[]={&l.add,&l.undo,&l.redo,&l.test,&l.clone,&l.remove,&l.save,&l.cancel};
  for(int i=0;i<8;++i)*tools[i]=(struct keymap_rect){x+6+(i%4)*unit,y+(i/4)*step,unit-3,rh};
  int top=y+2*step+(h<200?0:24),bottom=h-38;
  l.rows=(bottom-top)/28;if(l.rows<1)l.rows=1;if(l.rows>16)l.rows=16;
  int rowh=bottom-top<28?bottom-top:28;if(rowh<12)rowh=12;
  for(int i=0;i<l.rows;++i)l.properties[i]=(struct keymap_rect){x+6,top+i*28,pw-12,rowh-2};
  int nav=l.compact?(pw-68)/2:(pw-16)/2;
  l.up=(struct keymap_rect){x+6,h-36,nav,22};
  l.down=(struct keymap_rect){x+10+nav,h-36,nav,22};
  if(l.compact)l.toggle=(struct keymap_rect){x+pw-52,h-36,46,22};
  if(x>200){l.playback=(struct keymap_rect){12,48,132,26};l.visibility=(struct keymap_rect){12,78,132,26};}
  l.status=(struct keymap_rect){12,h-42,x>32?x-24:0,30};
  if(h>=200)l.detach=(struct keymap_rect){x+pw-60,76,54,20};
  return l;
}
static int keymap_ui_hit(struct keymap_layout l,int x,int y) {
  struct keymap_rect r[]={l.add,l.save,l.remove,l.tap,l.cancel,l.toggle,l.hold,l.playback,l.visibility,l.undo,l.redo,l.clone,l.test,l.up,l.down};
  int id[]={1,2,3,4,5,6,8,9,10,11,12,13,14,15,16};
  for(int i=0;i<15;++i)if(keymap_contains(r[i],x,y))return id[i];
  if(keymap_contains(l.detach,x,y))return 17;
  for(int i=0;i<l.rows;++i)if(keymap_contains(l.properties[i],x,y))return 20+i;
  if(keymap_contains(l.status,x,y))return -1;
  return keymap_contains(l.panel,x,y)?-1:0;
}
static const char *keymap_type_label(int kind) {
  static const char *names[]={"Tap / Hold","Movement joystick","Swipe","Repeat taps","Skill direction","Macro sequence","Absolute aim","Mouse touch","Mouse camera (FPS)","Right-click walk","FPS starter"};
  return kind>=0&&kind<=10?names[kind]:"Unknown";
}
static const char *keymap_type_description(int kind) {
  static const char *help[]={"Fixed touch point; keyboard or mouse","Four keys move one thumbstick","Drag from start to endpoint","Repeat while the trigger is held","Aim, cast, or cancel a skill","Bounded touch sequence (advanced)","Legacy pointer aim with fire point","Touch where the mouse clicks","Auto lock; Esc releases the mouse","Right button steers a thumbstick","Auto camera and separate M1 fire"};
  return kind>=0&&kind<=10?help[kind]:"";
}
static int keymap_palette_count(void) { return 11; }
static struct keymap_layout keymap_controls_layout(const struct keymap_overlay *f) {
  return keymap_layout(f->detached?f->panel.width:f->piece.width,
    f->detached?f->panel.height-keymap_panel_header(f):f->piece.height,0);
}
static struct keymap_rect keymap_type_rect(const struct keymap_overlay *f,int kind) {
  struct keymap_layout l=keymap_controls_layout(f);
  int i=kind-f->palette_scroll;
  return i>=0&&i<l.rows?l.properties[i]:(struct keymap_rect){0};
}
static int keymap_type_hit(const struct keymap_overlay *f,int x,int y) {
  struct keymap_layout l=keymap_controls_layout(f);
  if(keymap_contains(l.detach,x,y))return 17;
  if(keymap_contains(l.up,x,y))return 15;
  if(keymap_contains(l.down,x,y))return 16;
  for(int i=0;i<keymap_palette_count();++i)if(keymap_contains(keymap_type_rect(f,i),x,y))return 40+i;
  return -2;
}
/* Selecting a tool always starts a new placement, never silently converts the
 * currently selected control. Legacy imported types keep their semantics. */
static void keymap_editor_choose_kind(struct keymap_overlay *f,int kind) {
  f->placement_kind=kind;f->type_open=0;f->property_page=0;f->binding_capture=0;
  f->model.placing=1;f->model.selected=-1;f->model.error=0;f->inspector_scroll=0;
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
  if (r.w <= 0 || r.h <= 0)
    return;
  keymap_box(p, w, h, r, 6, active ? 0xff167cbb : 0xff293748);
  int scale=keymap_text_width(label,2)+12<=r.w?2:1;
  int size=keymap_text_size(scale);
  keymap_text_scaled_clipped(p,w,h,r.x+r.w-6,
                     r.x+(r.w-keymap_text_width(label,scale))/2,
                     r.y+(r.h-(size==11?8:11))/2,label,
                     enabled?0xffedf6ff:0xff8191a4,scale);
}
static int keymap_radius_pixels(const struct keymap_point *q,int w,int h) {
  if(q->radius)return (int)((int64_t)q->radius*(w<h?w:h)/65535);
  int dx=q->ex-q->nx,dy=q->ey-q->ny;if(dx<0)dx=-dx;if(dy<0)dy=-dy;
  int radius=dx>dy?dx:dy;if(radius<1024)radius=1024;if(radius>16384)radius=16384;
  return (int)((int64_t)radius*(w-1)/65535);
}
static int keymap_point_hit(const struct keymap_model *m, int w, int h, int x,
                            int y) {
  for (int i = m->count - 1; i >= 0; --i) {
    const struct keymap_point *q=&m->points[i];
    int hx=-100,hy=-100,code=0;
    if(q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_SKILL||q->kind==KEYMAP_AIM||q->kind==KEYMAP_MACRO){hx=(int)((int64_t)q->ex*(w-1)/65535);hy=(int)((int64_t)q->ey*(h-1)/65535);code=300;}
    if(q->kind==KEYMAP_DPAD){hx=(int)((int64_t)q->nx*(w-1)/65535)+keymap_radius_pixels(q,w,h);hy=(int)((int64_t)q->ny*(h-1)/65535);code=400;}
    if(q->kind==KEYMAP_LOOK){hx=(int)((int64_t)(q->nx+q->look_width)*(w-1)/65535);hy=(int)((int64_t)(q->ny+q->look_height)*(h-1)/65535);code=500;}
    if(code&&keymap_contains((struct keymap_rect){hx-9,hy-9,18,18},x,y))return code+i;
    int cx = (int)(((int64_t)m->points[i].nx * (w - 1) + 32767) / 65535);
    int cy = (int)(((int64_t)m->points[i].ny * (h - 1) + 32767) / 65535);
    if (keymap_contains((struct keymap_rect){cx + 17, cy - 33, 18, 18}, x, y))
      return 100 + i;
    if(q->kind!=KEYMAP_DPAD&&q->kind!=KEYMAP_WALK&&q->source!=KEYMAP_SOURCE_POINTER&&
       keymap_contains((struct keymap_rect){cx-24,cy-11,48,22},x,y))return 600+i;
    int dx = x - cx, dy = y - cy;
    if (dx < -28 || dx > 28 || dy < -28 || dy > 28)
      continue;
    if (dx * dx + dy * dy <= 28 * 28)
      return 200 + i;
  }
  return 0;
}

static int keymap_effective_hold(const struct keymap_point *q) {
  return q->end_mode==KEYMAP_END_HOLD||(q->end_mode==KEYMAP_END_LEGACY&&q->kind==KEYMAP_BASIC);
}
static void keymap_editor_set_end(struct keymap_point *q,int hold) {
  q->hold=hold;q->end_mode=hold?KEYMAP_END_HOLD:KEYMAP_END_RELEASE;
  if(!hold&&q->kind==KEYMAP_BASIC&&q->duration<50)q->duration=50;
}
/* Inspector rows are stable named fields, filtered for the selected control.
 * Numeric rows decrement on the left and increment on the right. */
enum { KP_BIND=1,KP_END,KP_DURATION,KP_REPEATS,KP_RADIUS,KP_RAMP,KP_WIDTH,KP_HEIGHT,
  KP_SENSX,KP_SENSY,KP_INVERT,KP_CAPTURE,KP_UP,KP_LEFT,KP_DOWN,KP_RIGHT,
  KP_DIRECTIONS,KP_WALK_KEY,KP_WALK_RADIUS,KP_SUSPEND,KP_DRAG,KP_SKILL_MODE,KP_CANCEL_KEY,
  KP_CANCEL_X,KP_CANCEL_Y,KP_SKILL_RADIUS };
static int keymap_inspector_fields(const struct keymap_overlay *f,int *out) {
  const struct keymap_model *m=&f->model;
  if(m->selected<0||m->selected>=m->count)return 0;
  const struct keymap_point *q=&m->points[m->selected];int n=0;
  if(q->kind!=KEYMAP_DPAD&&q->kind!=KEYMAP_WALK&&q->source!=KEYMAP_SOURCE_POINTER&&q->kind!=KEYMAP_WALK)out[n++]=KP_BIND;
  if(q->kind==KEYMAP_BASIC||q->kind==KEYMAP_SWIPE)out[n++]=KP_END;
  if((q->kind==KEYMAP_BASIC&&!keymap_effective_hold(q))||q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_REPEAT||q->kind==KEYMAP_MACRO)out[n++]=KP_DURATION;
  if(q->kind==KEYMAP_MACRO)out[n++]=KP_REPEATS;
  if(q->kind==KEYMAP_DPAD||q->kind==KEYMAP_WALK)out[n++]=KP_RADIUS;
  if(q->kind==KEYMAP_DPAD)out[n++]=KP_RAMP;
  if(q->kind==KEYMAP_DPAD){out[n++]=KP_DIRECTIONS;out[n++]=KP_UP;out[n++]=KP_LEFT;out[n++]=KP_DOWN;out[n++]=KP_RIGHT;out[n++]=KP_WALK_KEY;if(q->walk_key)out[n++]=KP_WALK_RADIUS;}
  if(q->kind==KEYMAP_LOOK){out[n++]=KP_CAPTURE;if(q->activation)out[n++]=KP_SUSPEND;out[n++]=KP_WIDTH;out[n++]=KP_HEIGHT;out[n++]=KP_SENSX;out[n++]=KP_SENSY;out[n++]=KP_INVERT;}
  if(q->kind==KEYMAP_BASIC&&q->source==KEYMAP_SOURCE_MOUSE&&keymap_effective_hold(q))out[n++]=KP_DRAG;
  if(q->kind==KEYMAP_SKILL){out[n++]=KP_SKILL_MODE;out[n++]=KP_SKILL_RADIUS;out[n++]=KP_CANCEL_KEY;if(q->cancel_key){out[n++]=KP_CANCEL_X;out[n++]=KP_CANCEL_Y;}}
  return n;
}
static const char *keymap_property_label(const struct keymap_overlay *f,int i,char *b,int n) {
  int fields[32],count=keymap_inspector_fields(f,fields),row=i+f->inspector_scroll;
  if(row>=count||row<0)return "";
  if(f->binding_capture&&fields[row]==f->capture_field){
    const char *name=fields[row]==KP_UP?"Up":fields[row]==KP_LEFT?"Left":fields[row]==KP_DOWN?"Down":fields[row]==KP_RIGHT?"Right":fields[row]==KP_WALK_KEY?"Walk modifier":fields[row]==KP_SUSPEND?"Show cursor":fields[row]==KP_CANCEL_KEY?"Cancel skill":"Binding";
    snprintf(b,n,"%s: press %s...",name,fields[row]==KP_BIND?"key / mouse":"key");return b;
  }
  if(f->numeric_capture&&fields[row]==f->numeric_field){snprintf(b,n,"Value: %d [Enter applies]",f->numeric_value);return b;}
  const struct keymap_point *q=&f->model.points[f->model.selected];char k[20];
  switch(fields[row]) {
    case KP_BIND:if(f->binding_capture)return "Press keyboard / mouse...";
      if(q->kind==KEYMAP_LOOK&&q->activation==KEYMAP_CAMERA_AUTO)
        snprintf(b,n,"Lock/unlock: %s [change]",keymap_point_label(q,k,sizeof(k)));
      else snprintf(b,n,"Binding: %s  [change]",keymap_point_label(q,k,sizeof(k)));
      break;
    case KP_END:return keymap_effective_hold(q)?"Touch: hold while pressed":"Touch: tap then release";
    case KP_DURATION:snprintf(b,n,"- Duration: %d ms +",q->duration);break;
    case KP_REPEATS:snprintf(b,n,"- Repeat count: %d +",q->repeats);break;
    case KP_RADIUS:snprintf(b,n,"- Joystick radius: %d%% +",q->radius*100/65535);break;
    case KP_RAMP:snprintf(b,n,"- Slide ramp: %d ms +",q->ramp_ms);break;
    case KP_DIRECTIONS:return "Direction preset: WASD / arrows";
    case KP_UP:case KP_LEFT:case KP_DOWN:case KP_RIGHT:{int j=fields[row]-KP_UP;int keys[]={q->up_key,q->left_key,q->down_key,q->right_key},fallback[]={17,30,31,32};const char *names[]={"Up","Left","Down","Right"};snprintf(b,n,"%s key: %s [change]",names[j],keymap_label(keys[j]?keys[j]:fallback[j],k,sizeof(k)));break;}
    case KP_WALK_KEY:snprintf(b,n,"Walk modifier: %s [change]",q->walk_key?keymap_label(q->walk_key,k,sizeof(k)):"none");break;
    case KP_WALK_RADIUS:snprintf(b,n,"- Walk radius: %d%% +",q->walk_radius?q->walk_radius:40);break;
    case KP_CAPTURE:{const char *status[]={"No API","Ready","Pending","Active"};
      int state=f->capture_status>=0&&f->capture_status<=3?f->capture_status:0;
      const char *modes[]={"hold","toggle","automatic"};
      snprintf(b,n,"Camera: %s [%s]",modes[q->activation],status[state]);break;}
    case KP_SUSPEND:snprintf(b,n,"Show cursor key: %s [change]",q->suspend_key?keymap_label(q->suspend_key,k,sizeof(k)):"none");break;
    case KP_WIDTH:case KP_HEIGHT:snprintf(b,n,"- Camera %s: %d%% +",fields[row]==KP_WIDTH?"width":"height",(fields[row]==KP_WIDTH?q->look_width:q->look_height)*200/65535);break;
    case KP_SENSX:case KP_SENSY:snprintf(b,n,"- Sensitivity %c: %d%% +",fields[row]==KP_SENSX?'X':'Y',fields[row]==KP_SENSX?q->sensitivity_x:q->sensitivity_y);break;
    case KP_INVERT:return q->invert_y?"Invert vertical: on":"Invert vertical: off";
    case KP_DRAG:return q->drag_shot?"Drag shot: on":"Drag shot: off";
    case KP_SKILL_MODE:{const char *modes[]={"release to cast","cast immediately","toggle casting"};snprintf(b,n,"Skill: %s",modes[q->skill_mode%3]);break;}
    case KP_SKILL_RADIUS:snprintf(b,n,"- Skill radius: %d%% +",q->radius*100/65535);break;
    case KP_CANCEL_KEY:snprintf(b,n,"Cancel skill key: %s [change]",q->cancel_key?keymap_label(q->cancel_key,k,sizeof(k)):"none");break;
    case KP_CANCEL_X:case KP_CANCEL_Y:snprintf(b,n,"- Cancel %c: %d%% +",fields[row]==KP_CANCEL_X?'X':'Y',(fields[row]==KP_CANCEL_X?q->cancel_x:q->cancel_y)*100/65535);break;
    default:return "";
  }
  return b;
}
static int keymap_property_enabled(const struct keymap_overlay *f,int row) {
  int fields[32],count=keymap_inspector_fields(f,fields);row+=f->inspector_scroll;
  if(row<0||row>=count)return 0;
  if(fields[row]==KP_CAPTURE)return f->model.points[f->model.selected].activation||f->capture_status>0;
  return 1;
}
static int keymap_editor_can_clone(const struct keymap_overlay *f) {
  const struct keymap_model *m=&f->model;
  if(m->selected<0||m->selected>=m->count||m->count>=KEYMAP_POINTS)return 0;
  int kind=m->points[m->selected].kind;
  return kind!=KEYMAP_LOOK&&kind!=KEYMAP_MOUSE_TOUCH&&kind!=KEYMAP_WALK;
}
static int keymap_same_controls(const struct keymap_model *a,const struct keymap_model *b) {
  return a->count==b->count&&!memcmp(a->points,b->points,(size_t)a->count*sizeof(a->points[0]));
}
static void keymap_history_init(struct keymap_overlay *f) {
  if(f->history)return;
  f->history=calloc(32,sizeof(*f->history));
  if(f->history){f->history[0]=f->model;f->history_count=1;f->history_index=0;}
}
static void keymap_history_commit(struct keymap_overlay *f) {
  if(!f->history)return;
  if(keymap_same_controls(&f->history[f->history_index],&f->model))return;
  if(f->history_index==31){for(int j=0;j<31;++j)f->history[j]=f->history[j+1];f->history_index=30;}
  f->history[++f->history_index]=f->model;f->history_count=f->history_index+1;
  if(f->baseline_valid)f->model.dirty=!keymap_same_controls(&f->baseline,&f->model);
}
static void keymap_history_step(struct keymap_overlay *f,int delta) {
  int i=f->history_index+delta;if(!f->history||i<0||i>=f->history_count)return;
  f->history_index=i;f->model=f->history[i];f->model.placing=0;
  f->model.error=0;f->binding_capture=0;f->dragging=0;f->inspector_scroll=0;
  if(f->baseline_valid)f->model.dirty=!keymap_same_controls(&f->baseline,&f->model);
}
static void keymap_property_action(struct keymap_overlay *f,int i,int delta) {
  int fields[32],n=keymap_inspector_fields(f,fields),row=i+f->inspector_scroll;
  if(row<0||row>=n)return;
  struct keymap_point *q=&f->model.points[f->model.selected];int field=fields[row];
  keymap_history_init(f);f->numeric_capture=0;
  if(field==KP_BIND||field==KP_UP||field==KP_LEFT||field==KP_DOWN||field==KP_RIGHT||field==KP_WALK_KEY||field==KP_SUSPEND||field==KP_CANCEL_KEY){f->binding_capture=1;f->capture_field=field;return;}
  int *v=0,step=1,min=0,max=65535;
  switch(field) {
    case KP_END:keymap_editor_set_end(q,!keymap_effective_hold(q));if(!keymap_effective_hold(q))q->drag_shot=0;break;
    case KP_DURATION:v=&q->duration;step=50;min=50;max=5000;break;
    case KP_REPEATS:v=&q->repeats;min=1;max=20;break;
    case KP_RADIUS:case KP_SKILL_RADIUS:v=&q->radius;step=1024;min=256;max=32767;break;
    case KP_RAMP:v=&q->ramp_ms;step=20;max=1000;break;
    case KP_DIRECTIONS:if(q->up_key==103){q->up_key=17;q->left_key=30;q->down_key=31;q->right_key=32;}else{q->up_key=103;q->left_key=105;q->down_key=108;q->right_key=106;}q->key=q->up_key;break;
    case KP_WALK_RADIUS:v=&q->walk_radius;step=10;min=10;max=100;if(!*v)*v=40;break;
    case KP_CAPTURE:if(q->activation||f->capture_status){
      q->activation=(q->activation+1)%3;
      if(!q->activation)q->suspend_key=0;
      if(q->activation!=KEYMAP_CAMERA_AUTO&&!q->key){f->binding_capture=1;f->capture_field=KP_BIND;}
    }break;
    case KP_WIDTH:case KP_HEIGHT:{int c=field==KP_WIDTH?q->nx:q->ny;v=field==KP_WIDTH?&q->look_width:&q->look_height;min=256;max=c<32768?c:65535-c;step=1024;break;}
    case KP_SENSX:case KP_SENSY:v=field==KP_SENSX?&q->sensitivity_x:&q->sensitivity_y;min=10;max=1000;step=10;break;
    case KP_INVERT:q->invert_y=!q->invert_y;break;
    case KP_DRAG:q->drag_shot=!q->drag_shot;break;
    case KP_SKILL_MODE:q->skill_mode=(q->skill_mode+1)%3;break;
    case KP_CANCEL_X:case KP_CANCEL_Y:v=field==KP_CANCEL_X?&q->cancel_x:&q->cancel_y;step=1024;break;
  }
  if(v&&!delta){f->numeric_capture=1;f->numeric_field=field;f->numeric_value=0;f->numeric_digits=0;return;}
  if(v){*v+=step*delta;if(*v<min)*v=min;if(*v>max)*v=max;}
  while(q->kind==KEYMAP_MACRO&&q->duration*q->repeats*2>10000&&q->duration>50)q->duration-=50;
  f->model.dirty=1;f->model.error=keymap_valid(&f->model,0)?0:1;keymap_history_commit(f);
}
static void keymap_numeric_apply(struct keymap_overlay *f) {
  if(f->model.selected<0||f->model.selected>=f->model.count){f->numeric_capture=0;return;}
  struct keymap_point *q=&f->model.points[f->model.selected],old=*q;
  int v=f->numeric_value,field=f->numeric_field;
  if(field==KP_DURATION)q->duration=v;
  if(field==KP_REPEATS)q->repeats=v;
  if(field==KP_RADIUS||field==KP_SKILL_RADIUS)q->radius=(int)((int64_t)v*65535/100);
  if(field==KP_RAMP)q->ramp_ms=v;
  if(field==KP_WALK_RADIUS)q->walk_radius=v;
  if(field==KP_WIDTH)q->look_width=(int)((int64_t)v*65535/200);
  if(field==KP_HEIGHT)q->look_height=(int)((int64_t)v*65535/200);
  if(field==KP_SENSX)q->sensitivity_x=v;
  if(field==KP_SENSY)q->sensitivity_y=v;
  if(field==KP_CANCEL_X)q->cancel_x=(int)((int64_t)v*65535/100);
  if(field==KP_CANCEL_Y)q->cancel_y=(int)((int64_t)v*65535/100);
  if(!keymap_valid(&f->model,0)){*q=old;f->model.error=2;return;}
  f->numeric_capture=0;f->model.error=0;f->model.dirty=1;keymap_history_commit(f);
}
static void keymap_editor_clear_optional(struct keymap_overlay *f) {
  if(f->model.selected<0||f->model.selected>=f->model.count)return;
  struct keymap_point *q=&f->model.points[f->model.selected];int field=f->capture_field;
  if(field==KP_WALK_KEY){q->walk_key=0;q->walk_radius=0;}
  else if(field==KP_SUSPEND)q->suspend_key=0;
  else if(field==KP_CANCEL_KEY){q->cancel_key=0;q->cancel_x=0;q->cancel_y=0;}
  else return;
  f->binding_capture=0;f->model.dirty=1;keymap_history_commit(f);
}
static int keymap_editor_assign(struct keymap_overlay *f,int source,int key) {
  if(f->model.selected<0||f->model.selected>=f->model.count)return 0;
  keymap_history_init(f);
  if(f->capture_field==KP_BIND||!f->capture_field){
    struct keymap_point *q=&f->model.points[f->model.selected];int drag=q->drag_shot;
    if(source!=KEYMAP_SOURCE_MOUSE)q->drag_shot=0;
    int ok=keymap_assign_source(&f->model,source,key);
    if(ok)keymap_history_commit(f);else q->drag_shot=drag;
    return ok;
  }
  if(source!=KEYMAP_SOURCE_KEY)return 0;
  if(key<2||key>767||(key>=60&&key<=62))return 0;
  struct keymap_point *q=&f->model.points[f->model.selected],old=*q;int field=f->capture_field;
  if(field>=KP_UP&&field<=KP_RIGHT){if(!q->up_key){q->up_key=17;q->left_key=30;q->down_key=31;q->right_key=32;}int *keys[]={&q->up_key,&q->left_key,&q->down_key,&q->right_key};*keys[field-KP_UP]=key;q->key=q->up_key;}
  if(field==KP_WALK_KEY)q->walk_key=key;
  if(field==KP_SUSPEND)q->suspend_key=key;
  if(field==KP_CANCEL_KEY)q->cancel_key=key;
  if(!keymap_valid(&f->model,0)){*q=old;f->model.error=1;return 0;}
  f->model.dirty=1;f->model.error=0;keymap_history_commit(f);return 1;
}
static void keymap_editor_scroll(struct keymap_overlay *f,int delta) {
  struct keymap_layout l=keymap_controls_layout(f);
  int fields[32],count=f->type_open?keymap_palette_count():keymap_inspector_fields(f,fields);
  int *offset=f->type_open?&f->palette_scroll:&f->inspector_scroll;
  int limit=count-l.rows;if(limit<0)limit=0;*offset+=delta;if(*offset<0)*offset=0;if(*offset>limit)*offset=limit;
}

static void keymap_control_ring(uint32_t *p,int w,int h,int cx,int cy,int r,uint32_t color) {
  int x=r,y=0,error=1-r;
  while(x>=y){
    int xs[]={x,y,-y,-x,-x,-y,y,x},ys[]={y,x,x,y,-y,-x,-x,-y};
    for(int i=0;i<8;++i){int px=cx+xs[i],py=cy+ys[i];if(px>=0&&px<w&&py>=0&&py<h)p[(size_t)py*w+px]=color;}
    ++y;if(error<0)error+=2*y+1;else{--x;error+=2*(y-x)+1;}
  }
}
static int keymap_control_problem(const struct keymap_model *m,int index) {
  const struct keymap_point *q=&m->points[index];
  if(!q->key&&q->source!=KEYMAP_SOURCE_POINTER)return 1;
  int keys[8],n=keymap_key_reservations(q,keys);
  for(int i=0;i<m->count;++i)if(i!=index){
    const struct keymap_point *p=&m->points[i];
    if(q->key&&q->key==p->key&&q->source==p->source)return 1;
    int other[8],count=keymap_key_reservations(p,other);
    for(int a=0;a<n;++a)for(int b=0;b<count;++b)if(keys[a]==other[b])return 1;
  }
  return 0;
}
static const char *keymap_control_caption(const struct keymap_point *q) {
  if(!q->kind)return q->source==KEYMAP_SOURCE_MOUSE&&q->key==272&&keymap_effective_hold(q)?"Fire":keymap_effective_hold(q)?"Hold":"Tap";
  const char *names[]={"","Move","Swipe","Repeat","Skill","Macro","Abs aim","Pointer","Camera","Walk"};
  return names[q->kind];
}
static int keymap_capture_row(const struct keymap_overlay *f,int row) {
  int fields[32],n=keymap_inspector_fields(f,fields);row+=f->inspector_scroll;
  return f->binding_capture&&row>=0&&row<n&&fields[row]==f->capture_field;
}
static void keymap_editor_paint(uint32_t *p,int w,int h,const struct keymap_overlay *f) {
  (void)keymap_text;
  const struct keymap_model *m=&f->model;
  struct keymap_layout l=keymap_layout(w,h,f->panel_hidden);
  if(f->testing){
    if(!f->panel_paint)keymap_hints_paint(p,w,h,f);
    keymap_ui_button(p,w,h,(struct keymap_rect){12,12,184,30},"Return to editing (F2)",1,1);
    if(f->panel_paint)keymap_text_scaled(p,w,h,12,62,"Click game window to try draft",0xffa8bed4,1);
    return;
  }
  for (int i = 0; !f->panel_paint&&i < m->count; ++i) {
    const struct keymap_point *q = &m->points[i];
    int cx = (int)(((int64_t)q->nx * (w - 1) + 32767) / 65535);
    int cy = (int)(((int64_t)q->ny * (h - 1) + 32767) / 65535);
    int chosen = i == m->selected;
    int problem=keymap_control_problem(m,i)||(chosen&&m->error==1);
    int hx=-100,hy=-100;
    if(q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_SKILL||q->kind==KEYMAP_AIM||q->kind==KEYMAP_MACRO){hx=(int)((int64_t)q->ex*(w-1)/65535);hy=(int)((int64_t)q->ey*(h-1)/65535);}
    if(q->kind==KEYMAP_DPAD){hx=cx+keymap_radius_pixels(q,w,h);hy=cy;}
    if(q->kind==KEYMAP_LOOK){hx=(int)((int64_t)(q->nx+q->look_width)*(w-1)/65535);hy=(int)((int64_t)(q->ny+q->look_height)*(h-1)/65535);
      int left=(int)((int64_t)(q->nx-q->look_width)*(w-1)/65535),top=(int)((int64_t)(q->ny-q->look_height)*(h-1)/65535);
      keymap_box(p,w,h,(struct keymap_rect){left,top,hx-left,2},0,0xff32b8f5);keymap_box(p,w,h,(struct keymap_rect){left,hy,hx-left,2},0,0xff32b8f5);
      keymap_box(p,w,h,(struct keymap_rect){left,top,2,hy-top},0,0xff32b8f5);keymap_box(p,w,h,(struct keymap_rect){hx,top,2,hy-top},0,0xff32b8f5);}
    if(q->kind==KEYMAP_SKILL&&q->radius)keymap_control_ring(p,w,h,cx,cy,keymap_radius_pixels(q,w,h),0xff7ad2c6);
    if(q->kind==KEYMAP_WALK)keymap_control_ring(p,w,h,cx,cy,keymap_radius_pixels(q,w,h),0xff32b8f5);
    if(q->kind==KEYMAP_SKILL&&q->cancel_key){
      int xx=(int)((int64_t)q->cancel_x*(w-1)/65535),yy=(int)((int64_t)q->cancel_y*(h-1)/65535);
      keymap_box(p,w,h,(struct keymap_rect){xx-10,yy-10,20,20},3,0xff8d3349);
      keymap_text_scaled(p,w,h,xx-3,yy-4,"X",0xfff6dce2,1);
    }
    if(q->kind==KEYMAP_DPAD){
      int r=keymap_radius_pixels(q,w,h),dx[]={0,-r,0,r},dy[]={-r,0,r,0};
      for(int d=0;d<4;++d){char b[20];const char *text=keymap_label(keymap_dpad_key(q,d),b,sizeof(b));keymap_text_scaled(p,w,h,cx+dx[d]-keymap_text_width(text,1)/2,cy+dy[d]-4,text,0xffc4e9ff,1);}
    }
    if(hx>=0){
      if(q->kind==KEYMAP_DPAD){int r=hx-cx;
        for(int yy=-r;yy<=r;++yy)for(int xx=-r;xx<=r;++xx){int d=xx*xx+yy*yy;
          if(d<r*r&&(r<2||d>(r-2)*(r-2))&&cx+xx>=0&&cx+xx<w&&cy+yy>=0&&cy+yy<h)p[(size_t)(cy+yy)*w+cx+xx]=0xff32b8f5;}}
      else if(q->kind!=KEYMAP_LOOK){int dx=hx-cx,dy=hy-cy,steps=(dx<0?-dx:dx)+(dy<0?-dy:dy);
        for(int k=0;k<=steps&&steps;++k){int xx=cx+dx*k/steps,yy=cy+dy*k/steps;if(xx>=0&&xx<w&&yy>=0&&yy<h)p[(size_t)yy*w+xx]=0xff32b8f5;}}
      keymap_box(p,w,h,(struct keymap_rect){hx-8,hy-8,16,16},3,0xffa8edff);
      keymap_text_scaled(p,w,h,hx-6,hy-4,q->kind==KEYMAP_DPAD?"R":q->kind==KEYMAP_LOOK?"+":q->kind==KEYMAP_SWIPE?">":"E",0xff172331,1);}
    for (int y = -31; y <= 31; ++y)
      for (int x = -31; x <= 31; ++x) {
        int d = x * x + y * y, px = cx + x, py = cy + y;
        if (d > 31 * 31 || px < 0 || px >= w || py < 0 || py >= h)
          continue;
        uint32_t color = 0;
        if (d <= 25 * 25)
          color = chosen ? 0xdd163751 : 0xc0142233;
        else if (d <= 28 * 28)
          color = problem?0xffffad99:chosen?0xffa8edff:0xff32b8f5;
        else if (chosen)
          color = 0x60305a60;
        if (color)
          p[(size_t)py * w + px] = color;
      }
    char key[20];
    const char *label = f->binding_capture&&chosen?"...":keymap_point_label(q, key, sizeof(key));
    int scale = keymap_text_width(label,2)>44?1:2;
    keymap_text_scaled(p, w, h, cx-keymap_text_width(label,scale)/2, cy - 7,
                       label, 0xfff3faff, scale);
    const char *caption=keymap_control_caption(q);
    keymap_text_scaled(p,w,h,cx-keymap_text_width(caption,1)/2,cy+12,caption,0xff8cdbff,1);
    keymap_box(p, w, h, (struct keymap_rect){cx + 17, cy - 33, 18, 18}, 4,
               0xff243b50);
    keymap_text_scaled(p, w, h, cx+26-keymap_text_width("X",1)/2, cy-28, "X", 0xff8bd8ff, 1);
  }

  const char *hint=f->save_pending?"Saving...":m->error==1?"Binding conflict":m->error==2?"Check value / missing binding":m->error==3?"Save failed":f->numeric_capture?"Type value; Enter applies; Esc cancels":f->binding_capture?"Press keyboard or mouse; Esc cancels":m->placing?"Click game canvas to place":"Click key label to bind; drag ring to move";
  if(f->detached&&!f->panel_paint){
    keymap_ui_button(p,w,h,(struct keymap_rect){12,12,60,26},"Dock",0,1);
    if(f->binding_capture||f->numeric_capture){struct keymap_rect r={8,h-34,w-16,26};keymap_box(p,w,h,r,5,m->error?0xff603443:0xff174f78);keymap_text_scaled_clipped(p,w,h,w-16,16,h-26,hint,m->error?0xffffad99:0xfff3faff,1);}
    return;
  }
  if(!f->panel_hidden){
    keymap_box(p,w,h,l.panel,8,0xfa19232f);
    keymap_ui_button(p,w,h,l.add,"Tools",f->type_open,1);
    keymap_ui_button(p,w,h,l.undo,"Undo",0,f->history_index>0);
    keymap_ui_button(p,w,h,l.redo,"Redo",0,f->history_index+1<f->history_count);
    keymap_ui_button(p,w,h,l.test,"Try",0,keymap_valid(m,1));
    keymap_ui_button(p,w,h,l.clone,"Clone",0,keymap_editor_can_clone(f));
    keymap_ui_button(p,w,h,l.remove,"Delete",0,m->selected>=0);
    keymap_ui_button(p,w,h,l.save,m->dirty?"Save*":"Save",1,!f->save_pending);
    keymap_ui_button(p,w,h,l.cancel,"Cancel",0,!f->save_pending);
    if(h>=200){char title[72];snprintf(title,sizeof(title),"%s",f->type_open?"Choose tool, then place":m->selected>=0?keymap_type_label(m->points[m->selected].kind):"Select a control to inspect");keymap_text_scaled_clipped(p,w,h,l.detach.x-4,l.panel.x+8,80,title,0xff93d9ff,1);
      keymap_ui_button(p,w,h,l.detach,f->detached?"Dock":"Detach",0,1);}
    for(int i=0;i<l.rows;++i){char label[100];
      if(f->type_open){int kind=i+f->palette_scroll;if(kind>=keymap_palette_count())break;
        struct keymap_rect r=l.properties[i];keymap_box(p,w,h,r,4,0xff293748);
        keymap_text_scaled_clipped(p,w,h,r.x+r.w-4,r.x+6,r.y+2,keymap_type_label(kind),0xffedf6ff,1);
        if(r.h>=24)keymap_text_scaled_clipped(p,w,h,r.x+r.w-4,r.x+6,r.y+14,keymap_type_description(kind),0xffa8bed4,1);
      }else{const char *text=keymap_property_label(f,i,label,sizeof(label));if(*text)keymap_ui_button(p,w,h,l.properties[i],text,keymap_capture_row(f,i),keymap_property_enabled(f,i));}
    }
    keymap_ui_button(p,w,h,l.up,"Scroll up",0,1);keymap_ui_button(p,w,h,l.down,"Scroll down",0,1);
    if(l.status.w){keymap_box(p,w,h,l.status,5,0xee172331);keymap_text_scaled_clipped(p,w,h,l.status.x+l.status.w-4,l.status.x+5,l.status.y+10,hint,m->error?0xffffad99:0xff89d5ff,1);}
    keymap_ui_button(p,w,h,l.playback,f->disabled?"F3: Keymap off":"F3: Keymap on",!f->disabled,1);
    keymap_ui_button(p,w,h,l.visibility,f->hints_hidden?"F4: Markers off":"F4: Markers on",!f->hints_hidden,1);
  }
  keymap_ui_button(p,w,h,l.toggle,f->panel_hidden?"Show":"Hide",0,1);
  if(f->panel_paint){struct keymap_rect r={8,h-68,w-16,26};keymap_box(p,w,h,r,5,m->error?0xff603443:0xff172331);keymap_text_scaled_clipped(p,w,h,w-16,16,h-60,hint,m->error?0xffffad99:0xff89d5ff,1);}
  if(f->binding_capture||f->numeric_capture){struct keymap_rect r={8,h-34,w-16,26};keymap_box(p,w,h,r,5,m->error?0xff603443:0xff174f78);keymap_text_scaled_clipped(p,w,h,w-16,16,h-26,hint,m->error?0xffffad99:0xfff3faff,1);}
}
