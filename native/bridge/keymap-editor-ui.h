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
  struct keymap_rect properties[8];
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
  int px=l.compact?12:w-236, py=l.compact?12:304;
  int pw=l.compact?w-88:208;
  int columns=!l.compact&&h>=540?2:4, row_height=columns==2?32:24;
  for(int i=0;i<8;++i)l.properties[i]=(struct keymap_rect){px+(i%columns)*pw/columns,
    py+(i/columns)*row_height,pw/columns-2,row_height-2};
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
  for(int i=0;i<8;++i)if(keymap_contains(l.properties[i],x,y))return 20+i;
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
    int dx = x - cx, dy = y - cy;
    if (dx < -28 || dx > 28 || dy < -28 || dy > 28)
      continue;
    if (dx * dx + dy * dy <= 28 * 28)
      return 200 + i;
  }
  return 0;
}
/* Paged properties share exactly the same hit rectangles at every size. */
static int keymap_effective_hold(const struct keymap_point *q) {
  return q->end_mode==KEYMAP_END_HOLD||
    (q->end_mode==KEYMAP_END_LEGACY&&q->kind==KEYMAP_BASIC);
}
static void keymap_editor_set_end(struct keymap_point *q,int hold) {
  q->hold=hold;
  q->end_mode=hold?KEYMAP_END_HOLD:KEYMAP_END_RELEASE;
  /* V1 Basic rows have no duration. An explicit conversion to a bounded tap
   * must acquire the same minimum as a newly authored Basic control. */
  if(!hold&&q->kind==KEYMAP_BASIC&&q->duration<50)q->duration=50;
}
static int keymap_page_available(const struct keymap_point *q,int page) {
  if(!page)return 1;
  if(!q)return 0;
  if(page==1)return (q->kind==KEYMAP_BASIC&&!keymap_effective_hold(q))||q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_REPEAT||q->kind==KEYMAP_MACRO;
  if(page==2)return q->kind==KEYMAP_DPAD;
  return q->kind==KEYMAP_LOOK;
}
static const char *keymap_property_label(const struct keymap_overlay *f,int i,char *b,int n) {
  const struct keymap_model *m=&f->model;
  const struct keymap_point *q=m->selected>=0&&m->selected<m->count?&m->points[m->selected]:0;
  if(i==0){int kind=q?q->kind:f->placement_kind;
    if(kind==KEYMAP_MOUSE_TOUCH)return "Touch >";
    snprintf(b,n,"%s >",keymap_kind_name(kind));return b;}
  if(i==1)return "< Page";
  if(i==2){const char *names[]={"Binding >","Timing >","Joystick >","Region >","Sensitivity >","Look >"};
    const char *short_names[]={"Bind >","Time >","Stick >","Region>","Sens >","Look >"};
    return f->piece.width<600||f->piece.height<420?short_names[f->property_page]:names[f->property_page];}
  if(i==3){if(f->binding_capture)return f->binding_capture==1?"Key...":"Mouse...";
    if(!q)return "Binding";
    if(q->source==KEYMAP_SOURCE_POINTER)return "Pointer";
    if(q->source==KEYMAP_SOURCE_MOUSE){snprintf(b,n,"Mouse%d",q->key-271);return b;}
    char key[20];snprintf(b,n,f->piece.width<600||f->piece.height<420?"K:%s":"Key:%s",keymap_label(q->key,key,sizeof(key)));return b;}
  if(!q)return "--";
  int j=i-4;
  if(q->kind==KEYMAP_MOUSE_TOUCH)return j==0?"Turn off":"--";
  if(f->property_page==0){if(j<2)return j?"Mouse...":"Key...";
    if(j==2)return q->source==KEYMAP_SOURCE_MOUSE?"Mouse":q->source==KEYMAP_SOURCE_POINTER?"Pointer":"Keyboard";
    return q->kind==KEYMAP_BASIC||q->kind==KEYMAP_SWIPE?(keymap_effective_hold(q)?"Hold":"Release"):"--";}
  if(f->property_page==1){snprintf(b,n,j<2?"%dms %c":"x%d %c",j<2?q->duration:q->repeats,j%2?'+':'-');return b;}
  if(f->property_page==2){if(j<2){if(!q->radius)snprintf(b,n,"Auto %c",j%2?'+':'-');else snprintf(b,n,"R%d%% %c",q->radius*100/65535,j%2?'+':'-');}else snprintf(b,n,"%dms %c",q->ramp_ms,j%2?'+':'-');return b;}
  if(f->property_page==3){snprintf(b,n,j<2?"W%d%% %c":"H%d%% %c",(j<2?q->look_width:q->look_height)*200/65535,j%2?'+':'-');return b;}
  if(f->property_page==4){snprintf(b,n,j<2?"X%d%% %c":"Y%d%% %c",j<2?q->sensitivity_x:q->sensitivity_y,j%2?'+':'-');return b;}
  if(j==0)return q->invert_y?"Y inverted":"Y normal";
  if(j==1)return q->activation?"Capture":f->capture_status?"Hold":"No FPS";
  if(j==2)return "Esc exit";
  if(q->kind==KEYMAP_LOOK&&q->activation){const char *status[]={"No API","Ready","Pending","Active"};return status[f->capture_status>=0&&f->capture_status<=3?f->capture_status:0];}
  return q->source==KEYMAP_SOURCE_POINTER?"Pointer":q->source==KEYMAP_SOURCE_MOUSE?"Mouse":"Keyboard";
}
static int keymap_property_enabled(const struct keymap_overlay *f,int i) {
  if(i<3)return 1;
  const struct keymap_model *m=&f->model;
  if(m->selected<0||m->selected>=m->count)return 0;
  const struct keymap_point *q=&m->points[m->selected];
  if(i==3)return 0;
  int j=i-4,p=f->property_page;
  if(q->kind==KEYMAP_MOUSE_TOUCH)return j==0;
  if(p==0)return j<2&&q->kind!=KEYMAP_DPAD&&q->kind!=KEYMAP_MOUSE_TOUCH;
  if(p==1)return keymap_page_available(q,1)&&(j<2||q->kind==KEYMAP_MACRO);
  if(p==2)return q->kind==KEYMAP_DPAD;
  if(p==3||p==4)return q->kind==KEYMAP_LOOK;
  return j<2&&q->kind==KEYMAP_LOOK&&(j!=1||q->activation||f->capture_status);
}
static void keymap_property_action(struct keymap_overlay *f,int i) {
  struct keymap_model *m=&f->model;
  struct keymap_point *q=m->selected>=0&&m->selected<m->count?&m->points[m->selected]:0;
  if(!keymap_property_enabled(f,i))return;
  if(i==1||i==2){do{f->property_page=(f->property_page+(i==1?5:1))%6;}while(!keymap_page_available(q,f->property_page));return;}
  if(i==0){f->placement_kind=((q?q->kind:f->placement_kind)+1)%(KEYMAP_LOOK+1);
    if(q){keymap_set_kind(q,f->placement_kind);m->dirty=1;m->error=keymap_valid(m,0)?0:1;}
    f->property_page=0;f->binding_capture=0;return;}
  int j=i-4,delta=j%2?1:-1;
  if(q->kind==KEYMAP_MOUSE_TOUCH){keymap_remove(m);return;}
  if(f->property_page==0)f->binding_capture=j+1;
  if(f->property_page==1){if(j<2){q->duration+=delta*50;if(q->duration<50)q->duration=50;if(q->duration>5000)q->duration=5000;}else{q->repeats+=delta;if(q->repeats<1)q->repeats=1;if(q->repeats>20)q->repeats=20;}while(q->kind==KEYMAP_MACRO&&q->duration*q->repeats*2>10000)q->duration-=50;}
  if(f->property_page==2){int *v=j<2?&q->radius:&q->ramp_ms;*v+=delta*(j<2?1024:20);if(*v<(j<2?256:0))*v=j<2?256:0;if(*v>(j<2?32767:1000))*v=j<2?32767:1000;}
  if(f->property_page==3){int *v=j<2?&q->look_width:&q->look_height,c=j<2?q->nx:q->ny,limit=c<32768?c:65535-c;*v+=delta*1024;if(*v<256)*v=256;if(*v>limit)*v=limit;}
  if(f->property_page==4){int *v=j<2?&q->sensitivity_x:&q->sensitivity_y;*v+=delta*10;if(*v<10)*v=10;if(*v>1000)*v=1000;}
  if(f->property_page==5){if(j==0)q->invert_y=!q->invert_y;if(j==1)q->activation=!q->activation;}
  m->dirty=1;
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
    int hx=-100,hy=-100;
    if(q->kind==KEYMAP_SWIPE||q->kind==KEYMAP_SKILL||q->kind==KEYMAP_AIM||q->kind==KEYMAP_MACRO){hx=(int)((int64_t)q->ex*(w-1)/65535);hy=(int)((int64_t)q->ey*(h-1)/65535);}
    if(q->kind==KEYMAP_DPAD){hx=cx+keymap_radius_pixels(q,w,h);hy=cy;}
    if(q->kind==KEYMAP_LOOK){hx=(int)((int64_t)(q->nx+q->look_width)*(w-1)/65535);hy=(int)((int64_t)(q->ny+q->look_height)*(h-1)/65535);
      int left=(int)((int64_t)(q->nx-q->look_width)*(w-1)/65535),top=(int)((int64_t)(q->ny-q->look_height)*(h-1)/65535);
      keymap_box(p,w,h,(struct keymap_rect){left,top,hx-left,2},0,0xff32b8f5);keymap_box(p,w,h,(struct keymap_rect){left,hy,hx-left,2},0,0xff32b8f5);
      keymap_box(p,w,h,(struct keymap_rect){left,top,2,hy-top},0,0xff32b8f5);keymap_box(p,w,h,(struct keymap_rect){hx,top,2,hy-top},0,0xff32b8f5);}
    if(hx>=0){
      if(q->kind==KEYMAP_DPAD){int r=hx-cx;
        for(int yy=-r;yy<=r;++yy)for(int xx=-r;xx<=r;++xx){int d=xx*xx+yy*yy;
          if(d<r*r&&(r<2||d>(r-2)*(r-2))&&cx+xx>=0&&cx+xx<w&&cy+yy>=0&&cy+yy<h)p[(size_t)(cy+yy)*w+cx+xx]=0xff32b8f5;}}
      else if(q->kind!=KEYMAP_LOOK){int dx=hx-cx,dy=hy-cy,steps=(dx<0?-dx:dx)+(dy<0?-dy:dy);
        for(int k=0;k<=steps&&steps;++k){int xx=cx+dx*k/steps,yy=cy+dy*k/steps;if(xx>=0&&xx<w&&yy>=0&&yy<h)p[(size_t)yy*w+xx]=0xff32b8f5;}}
      keymap_box(p,w,h,(struct keymap_rect){hx-8,hy-8,16,16},3,0xffa8edff);
      keymap_text_scaled(p,w,h,hx-6,hy-4,q->kind==KEYMAP_DPAD?"R":q->kind==KEYMAP_LOOK?"+":"E",0xff172331,1);}
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
    if (keymap_effective_hold(q) || q->kind)
      keymap_text_scaled(p, w, h, cx-keymap_text_width(q->kind?keymap_kind_name(q->kind):"Hold",1)/2, cy + 12, q->kind?keymap_kind_name(q->kind):"Hold", 0xff8cdbff, 1);
    keymap_box(p, w, h, (struct keymap_rect){cx + 17, cy - 33, 18, 18}, 4,
               0xff243b50);
    keymap_text_scaled(p, w, h, cx+26-keymap_text_width("X",1)/2, cy-28, "X", 0xff8bd8ff, 1);
  }
  static const char *pages[]={"Binding: Key / Mouse / end behavior","Timing: duration / macro repeats",
    "Joystick: radius / ramp milliseconds","Look region: width / height",
    "Look: X / Y sensitivity percent","Look: invert Y / hold or FPS capture"};
  const char *hint = f->save_pending ? "Saving..."
                     : m->error == 1 ? "Key already used"
                     : m->error == 2 ? "Check keys and WASD"
                     : m->error == 3 ? "Save failed"
                     : m->placing    ? "Click to place a control"
                     : f->binding_capture==1 ? "Press binding key / Esc cancel"
                     : f->binding_capture==2 ? "Press mouse button / Esc cancel"
                     : selected      ? pages[f->property_page%6]
                                     : "Select or add a control";
  if (!f->panel_hidden&&!l.compact) {
    keymap_box(p, w, h,
               (struct keymap_rect){12, 12, w < 360 ? w - 88 : 236, 48}, 8,
               0xee172331);
    char title[64];
    if(selected&&m->points[m->selected].kind==KEYMAP_MACRO)
      snprintf(title,sizeof(title),"Macro %dms x%d",m->points[m->selected].duration,m->points[m->selected].repeats);
    else if(selected&&(m->points[m->selected].kind==KEYMAP_SWIPE||m->points[m->selected].kind==KEYMAP_REPEAT))
      snprintf(title,sizeof(title),"%s %dms",keymap_kind_name(m->points[m->selected].kind),m->points[m->selected].duration);
    else if(selected&&m->points[m->selected].kind)
      snprintf(title,sizeof(title),"%s controls",keymap_kind_name(m->points[m->selected].kind));
    else snprintf(title,sizeof(title),selected?"Basic touch control":"Keyboard mapping");
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
      if (h >= 600) {
        keymap_text_scaled(p, w, h, x + 16, 452, "Type / binding / timing pages",
                           0xff9cadc0, 1);
        keymap_text_scaled(p, w, h, x + 16, 469, "Drag E endpoint / R radius",
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
    int hold = selected ? keymap_effective_hold(&m->points[m->selected]) : f->placement_hold;
    keymap_ui_button(p, w, h, l.add, l.compact ? "Add" : "Add control", m->placing,
                     m->count < 64);
    int end_enabled=!selected||m->points[m->selected].kind==KEYMAP_BASIC||m->points[m->selected].kind==KEYMAP_SWIPE;
    keymap_ui_button(p, w, h, l.tap,
                     l.compact ? (hold ? "Hold" : "Tap") : "Release", !hold, end_enabled);
    keymap_ui_button(p, w, h, l.hold, "Hold", hold, end_enabled);
    keymap_ui_button(p, w, h, l.remove, l.compact ? "Del" : "Delete control", 0,
                     selected);
    keymap_ui_button(p, w, h, l.save, f->save_pending ? "Wait" : m->dirty?"Save*":"Save", 1,
                     !f->save_pending);
    keymap_ui_button(p, w, h, l.cancel, l.compact ? "Exit" : "X", 0, 1);
  }
  keymap_ui_button(p, w, h, l.toggle, f->panel_hidden ? "Show" : "Hide", 0, 1);
  if(!f->panel_hidden)for(int i=0;i<8;++i){char label[40];
    int active=i==0;
    if(selected&&f->property_page==5) {
      if(i==4)active=m->points[m->selected].invert_y;
      if(i==5)active=m->points[m->selected].activation;
    }
    keymap_ui_button(p,w,h,l.properties[i],keymap_property_label(f,i,label,sizeof(label)),
      active,keymap_property_enabled(f,i));}
}
