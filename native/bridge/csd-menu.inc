/* Transient menu on the app's own surface. A transparent input cover consumes
 * outside clicks; no Android taps leak through the menu. Only menu events
 * repaint it. Edit keymap uses the same task-scoped request as F2. */
#include "csd-menu-model.h"
#include "menu-font.h"
#include "csd-size-model.h"
static int csd_menu_row(struct csd_frame *f,int x,int y,int w,int h) {
  int row=csd_menu_row_at_offset(x,y,w,h,f->menu_submenu,f->menu_scroll);
  return row;
}
static int csd_menu_enabled(struct csd_frame *f,int row) {
  struct object *s=f->top->surface;
  if(!s||s->overlay||s->resize.dragging)return 0;
  if(row==2||row==3) {if(f->menu_submenu!=1)return 0;}
  if(row>=CSD_MENU_PRESET_FIRST&&f->menu_submenu!=2)return 0;
  if(row>=1&&row<=CSD_MENU_CUSTOM) {
    if(s->full_ui)return row>=CSD_MENU_SIZE&&scale_has_viewporter;
    return s->app&&s->app_id&&s->viewport&&scale_has_viewporter;
  }
  return row==0&&ANVIL_KEYMAP_EDITOR&&keymap_connection_ready()&&
    getenv("ANVILDROID_RUNTIME_GENERATION")&&keymap_task_owner(s);
}
static void csd_menu_panel(uint32_t *pixels,int w,int h,struct csd_frame *f,
                           int px,int py,int width,int submenu,int rows) {
  if(px+width>w)width=w-px;
  int bottom=py+2*CSD_MENU_PAD+rows*CSD_MENU_ROW;
  for(int y=py;y<h&&y<bottom;++y)for(int x=px;x<px+width;++x) {
    int row=csd_menu_row(f,x,y,w,h);
    uint32_t color=0xff282828;
    if(x==px||x==px+width-1||y==py||y==bottom-1)color=0xff454545;
    else if(row>=0&&row==f->menu_hover&&csd_menu_enabled(f,row))color=0xff414141;
    pixels[(size_t)y*w+x]=color;
  }
  for(int n=0;n<rows;++n) {
    int compact=submenu&&csd_menu_inline(w,h,submenu);
    int action=csd_menu_action(submenu,n,compact,f->menu_scroll);
    int baseline=py+CSD_MENU_PAD+20+n*CSD_MENU_ROW;
    uint32_t ink=csd_menu_enabled(f,action)?0xffdedede:0xff777777;
    int text_x=px+12;
    const char *label=action==0?"Edit keymap":action==1?"Resize":
      action==2?"Resize app layout":action==3?"Keep resolution":action==4?"Size":
      action<=CSD_MENU_PRESET_LAST?csd_menu_sizes[action-CSD_MENU_PRESET_FIRST].label:
      action==CSD_MENU_CUSTOM?"Custom":action==CSD_MENU_LANDSCAPE?"Landscape":"Portrait";
    if(action==2||action==3) {
      int selected=resize_mode_fixed(f->top->surface->app_id)==(action==3);
      menu_text(pixels,w,h,px+width-8,px+12,baseline,selected?"*":"",ink,14);
      text_x=px+30;
    } else if(action>=CSD_MENU_PRESET_FIRST) {
      struct object *s=f->top->surface;
      int cw=s->fit_w?s->fit_w:s->content_width,ch=s->fit_h?s->fit_h:s->content_height;
      if(s->full_ui){cw=display_projection.lw?display_projection.lw:s->full_w;ch=display_projection.lh?display_projection.lh:s->full_h;}
      int selected=action==CSD_MENU_CUSTOM?s->custom_size.enabled:action<=CSD_MENU_PRESET_LAST?
        !s->custom_size.enabled&&cw==csd_menu_sizes[action-CSD_MENU_PRESET_FIRST].width&&ch==csd_menu_sizes[action-CSD_MENU_PRESET_FIRST].height:
        action==CSD_MENU_LANDSCAPE?cw>ch:ch>cw;
      menu_text(pixels,w,h,px+width-8,px+12,baseline,selected?"*":"",ink,14);
      text_x=px+30;
    } else if(submenu&&action==csd_menu_parent(submenu)&&compact) {
      menu_text(pixels,w,h,px+width-8,px+12,baseline,"<",ink,14);text_x=px+30;
    }
    menu_text(pixels,w,h,px+width-8,text_x,baseline,label,ink,14);
    if(action==0||action==1||action==4)menu_text(pixels,w,h,px+width-8,px+width-28,baseline,
      action==0?"F2":compact?"":">",ink,14);
    if(submenu&&action>=csd_menu_first(submenu)) {
      int first_row=compact?1:0;
      const char *hint=n==first_row&&f->menu_scroll>0?"^":
        n==rows-1&&f->menu_scroll+csd_menu_visible(w,h,submenu)<csd_menu_count(submenu)?"v":"";
      menu_text(pixels,w,h,px+width-8,px+width-24,baseline,hint,ink,14);
    }
  }
}
static void csd_menu_paint(uint32_t *pixels,int w,int h) {
  struct csd_frame *f=csd_menu_paint_context;if(!f)return;
  if(f->menu_submenu) {
    int limit=csd_menu_count(f->menu_submenu)-csd_menu_visible(w,h,f->menu_submenu);
    if(f->menu_scroll>limit)f->menu_scroll=limit;
  }
  if(f->menu_submenu&&csd_menu_inline(w,h,f->menu_submenu))
    csd_menu_panel(pixels,w,h,f,0,0,CSD_SUBMENU_WIDTH,f->menu_submenu,
      1+csd_menu_visible(w,h,f->menu_submenu));
  else {
    csd_menu_panel(pixels,w,h,f,0,0,CSD_MENU_WIDTH,0,CSD_MENU_ROWS);
    if(f->menu_submenu)
      csd_menu_panel(pixels,w,h,f,CSD_MENU_WIDTH,(f->menu_submenu==2?2:1)*CSD_MENU_ROW,
        CSD_SUBMENU_WIDTH,f->menu_submenu,csd_menu_visible(w,h,f->menu_submenu));
  }
}
static void csd_menu_sync(struct csd_frame *f,int x,int y,int w,int h) {
  struct csd_piece *p=&f->menu;
  if(f->fullscreen)f->menu_open=0;
  if(!f->menu_open||w<1||h<1) {
    if(p->surface&&p->width) {
      union argument empty[3]={{.o=0},{.i=0},{.i=0}};
      wayland_send(p->surface,1,empty);wayland_send(p->surface,6,0);
      p->width=p->height=0;
    }
    return;
  }
  if(!csd_compositor||!csd_subcompositor||!csd_shm)return;
  if(!p->surface) {
    union argument id={.o=0};
    p->surface=real_marshal(csd_compositor,0,&id,csd_surface_i,1);
    if(!p->surface)return;
    union argument sub[3]={{.o=0},{.o=p->surface},{.o=f->top->surface->p}};
    p->subsurface=real_marshal(csd_subcompositor,1,sub,csd_subsurface_i,1);
    if(!p->subsurface){wayland_send(p->surface,0,0);real_destroy(p->surface);p->surface=0;return;}
  }
  int position_changed=f->menu_x!=x||f->menu_y!=y;
  f->menu_x=x;f->menu_y=y;
  union argument pos[2]={{.i=x},{.i=y}};wayland_send(p->subsurface,1,pos);
  /* Keep it above Android layers, keymap hints and fit bars. */
  for(struct object *o=objects;o;o=o->next) {
    if(o->parent!=f->top->surface||!o->surface||!object_is(o,"wl_subsurface"))continue;
    union argument sibling={.o=o->surface->p};wayland_send(p->subsurface,2,&sibling);break;
  }
  struct object *s=f->top->surface;
  /* place_above inserts immediately above a sibling; repeating it for
   * unordered bars can lower the menu beneath earlier bars. Put each bar
   * below the menu instead, so all of them stay below it regardless of order. */
  union argument menu_sibling={.o=p->surface};
  if(s->fit)for(int i=0;i<4;++i)if(s->fit->bars[i].subsurface)
    wayland_send(s->fit->bars[i].subsurface,3,&menu_sibling);
  /* Full Android UI owns its letterbox bars on the frame instead of fit. */
  for(int i=0;i<4;++i)if(f->bars[i].subsurface)
    wayland_send(f->bars[i].subsurface,3,&menu_sibling);
  if(s->hints&&s->hints->piece.subsurface)
    wayland_send(s->hints->piece.subsurface,3,&menu_sibling);
  if(p->width!=w||p->height!=h||position_changed) {
    csd_menu_paint_context=f;
    struct proxy *buffer=csd_paint(6,w,h,0);
    csd_menu_paint_context=0;
    if(!buffer){
      f->menu_open=0;
      union argument empty[3]={{.o=0},{.i=0},{.i=0}};
      wayland_send(p->surface,1,empty);wayland_send(p->surface,6,0);
      p->width=p->height=0;return;
    }
    union argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
    union argument damage[4]={{.i=0},{.i=0},{.i=w},{.i=h}};
    wayland_send(p->surface,1,attach);wayland_send(p->surface,2,damage);
    p->width=w;p->height=h;
  }
  wayland_send(p->surface,6,0);
}
static void csd_menu_refresh(struct csd_frame *f) {
  struct object *s=f->top->surface;
  int w=f->menu.width,h=f->menu.height;
  f->menu.width=f->menu.height=0;
  if(w<1)w=s->preview_width?s->preview_width:s->content_width;
  if(h<1)h=s->preview_height?s->preview_height:s->content_height;
  if(window_fit_enabled(s)){w=s->fit_w;h=s->fit_h;}
  if(s->full_ui){w=s->full_w;h=s->full_h;}
  csd_menu_sync(f,s->full_ui?0:s->bx,s->full_ui?0:s->by,w,h);wayland_send(s->p,6,0);
}
static void csd_menu_close(struct csd_frame *f) {
  f->menu_open=0;f->menu_submenu=0;f->menu_scroll=0;csd_menu_sync(f,0,0,0,0);
  if(f->top->surface)wayland_send(f->top->surface->p,6,0);
}
static void csd_menu_toggle(struct csd_frame *f) {
  if(f->menu_open){csd_menu_close(f);return;}
  if((!f->top->surface->app&&!f->top->surface->full_ui)||f->fullscreen)return;
  for(struct object *o=objects;o;o=o->next)if(o->csd&&o->csd!=f&&o->csd->menu_open)csd_menu_close(o->csd);
  f->menu_open=1;f->menu_submenu=0;f->menu_scroll=0;f->menu_hover=-1;f->menu_pressed=-2;
  csd_menu_refresh(f);
}
static void csd_menu_destroy(struct csd_frame *f) {
  struct csd_piece *p=&f->menu;
  if(p->subsurface){wayland_send(p->subsurface,0,0);real_destroy(p->subsurface);}
  if(p->surface){wayland_send(p->surface,0,0);real_destroy(p->surface);}
}
static int csd_menu_enter(struct object *o,struct proxy *surface) {
  for(struct object *s=objects;s;s=s->next)if(s->csd&&s->csd->menu_open&&s->csd->menu.surface==surface) {
    o->menu_focus=s->csd;csd_menu_motion(o);return 1;
  }
  return 0;
}
static void csd_menu_motion(struct object *o) {
  struct csd_frame *f=o->menu_focus;if(!f||!f->menu_open)return;
  int row=csd_menu_row(f,o->csd_x,o->csd_y,f->menu.width,f->menu.height);
  if(row!=f->menu_hover){f->menu_hover=row;csd_menu_refresh(f);}
}
static void csd_menu_scroll(struct object *o,int step) {
  struct csd_frame *f=o->menu_focus;
  if(!f||!f->menu_open||!f->menu_submenu)return;
  int limit=csd_menu_count(f->menu_submenu)-csd_menu_visible(f->menu.width,f->menu.height,f->menu_submenu);
  int scroll=f->menu_scroll+step;
  if(scroll<0)scroll=0;
  if(scroll>limit)scroll=limit;
  if(scroll==f->menu_scroll)return;
  f->menu_scroll=scroll;f->menu_pressed=-2;
  f->menu_hover=csd_menu_row(f,o->csd_x,o->csd_y,f->menu.width,f->menu.height);
  csd_menu_refresh(f);
}
static void csd_menu_size(struct csd_frame *f,int width,int height) {
  struct object *s=f->top->surface;
  if(width<160||height<64||width>8192||height>8192)return;
  s->custom_size=(struct custom_size){0};
  if(s->full_ui) {
    if(!display_size_request(width,height,1))return;
    f->restore_width=width;f->restore_height=height;
    if(f->maximized)wayland_send(f->top->p,10,0);
    csd_menu_close(f);return;
  }
  width=window_layout_width(s,width);
  s->profile_loaded=1;s->profile_restoring=0;s->profile_recreated=0;
  s->profile_dirty=1;s->profile_ticks=0;
  s->profile_state_dirty=1;
  f->restore_width=width;f->restore_height=height;
  f->maximize_pending=f->maximize_retry=0;
  if(f->maximized) {
    f->size_pending=1;
    wayland_send(f->top->p,10,0); /* unset_maximized; configure uses this size */
  }
  f->maximized=0;s->resize.maximized=0;
  s->fit_w=width;s->fit_h=height;
  s->preview_width=s->preview_height=0;
  if(!game_fit_enabled(s)) {
    s->resize.ready=1;
    resize_configure(&s->resize,width,height,s->fit_delivered_w?s->fit_delivered_w:s->content_width,
      s->fit_delivered_h?s->fit_delivered_h:s->content_height,0,0);
  } else {
    s->resize.width=s->resize.height=s->resize.dirty=s->resize.quiet_ticks=0;
  }
  csd_menu_close(f);geometry(s);wayland_send(s->p,6,0);
}
static void csd_menu_activate(struct csd_frame *f,int row) {
  if(row<0){csd_menu_close(f);return;}
  if(!csd_menu_enabled(f,row))return;
  struct object *s=f->top->surface;
  if(row==CSD_MENU_RESIZE||row==CSD_MENU_SIZE) {
    int submenu=row==CSD_MENU_SIZE?2:1;
    f->menu_submenu=f->menu_submenu==submenu?0:submenu;
    f->menu_scroll=0;
    f->menu_hover=f->menu_submenu?csd_menu_first(submenu):row;
    f->menu_pressed=-2;
    csd_menu_refresh(f);
    return;
  }
  if(row==CSD_MENU_CUSTOM) {
    s->custom_size.enabled=1;
    if(s->full_ui)custom_size_queue(&s->custom_size,s->full_w,s->full_h,0);
    else {
      resize_mode_set(s->app_id,0);
      csd_menu_size(f,s->fit_w?s->fit_w:s->content_width,s->fit_h?s->fit_h:s->content_height);
      s->custom_size.enabled=1;
    }
    csd_menu_close(f);return;
  }
  if(row>=CSD_MENU_PRESET_FIRST&&row<=CSD_MENU_PRESET_LAST) {
    int index=row-CSD_MENU_PRESET_FIRST;
    csd_menu_size(f,csd_menu_sizes[index].width,csd_menu_sizes[index].height);
    return;
  }
  if(row==CSD_MENU_LANDSCAPE||row==CSD_MENU_PORTRAIT) {
    int w=s->fit_w?s->fit_w:s->content_width,h=s->fit_h?s->fit_h:s->content_height;
    if(s->full_ui){w=display_projection.lw?display_projection.lw:s->full_w;h=display_projection.lh?display_projection.lh:s->full_h;}
    if((row==CSD_MENU_LANDSCAPE&&w<h)||(row==CSD_MENU_PORTRAIT&&w>h)) {int swap=w;w=h;h=swap;}
    csd_menu_size(f,w,h);return;
  }
  if(row==2||row==3) {
    s->custom_size=(struct custom_size){0};
    resize_mode_set(s->app_id,row==3);
    csd_menu_close(f);
    geometry(s);
    wayland_send(s->p,6,0);
    return;
  }
  keymap_shortcut_task=keymap_task_owner(s);
  snprintf(keymap_shortcut_package,sizeof(keymap_shortcut_package),"%s",keymap_package(s));
  csd_menu_close(f);keymap_wake();
}
static void csd_menu_button(struct object *o,uint32_t button,uint32_t state) {
  struct csd_frame *f=o->menu_focus;if(!f||!f->menu_open||button!=0x110)return;
  int row=csd_menu_row(f,o->csd_x,o->csd_y,f->menu.width,f->menu.height);
  if(state)f->menu_pressed=row;
  else {if(row==f->menu_pressed)csd_menu_activate(f,row);f->menu_pressed=-2;}
}
static int csd_menu_key(struct object *o,uint32_t key,uint32_t state) {
  struct object *s=o->keyboard_focus;
  if(!s||!s->csd||!s->csd->menu_open||key>=768)return 0;
  struct csd_frame *f=s->csd;
  if(state)o->keymap_blocked_keys[key/32]|=1u<<(key%32);
  else o->keymap_blocked_keys[key/32]&=~(1u<<(key%32));
  if(!state)return 1;
  if(key==1||key==105) {
    if(f->menu_submenu){f->menu_hover=csd_menu_parent(f->menu_submenu);f->menu_submenu=0;f->menu_scroll=0;csd_menu_refresh(f);}
    else if(key==1)csd_menu_close(f);
  }
  else if(key==106&&(f->menu_hover==1||f->menu_hover==CSD_MENU_SIZE)&&!f->menu_submenu)csd_menu_activate(f,f->menu_hover);
  else if(key==28&&f->menu_hover>=0)csd_menu_activate(f,f->menu_hover);
  else if(key==103||key==108) {
    int first=f->menu_submenu?csd_menu_first(f->menu_submenu):0;
    int count=f->menu_submenu?csd_menu_count(f->menu_submenu):CSD_MENU_ROWS;
    int at=!f->menu_submenu&&f->menu_hover==CSD_MENU_SIZE?2:f->menu_hover-first;
    if(at<0||at>=count)at=key==103?count-1:0;
    else at=(at+(key==103?count-1:1))%count;
    f->menu_hover=csd_menu_action(f->menu_submenu,at,0,0);
    if(f->menu_submenu) {
      int visible=csd_menu_visible(f->menu.width,f->menu.height,f->menu_submenu);
      if(at<f->menu_scroll)f->menu_scroll=at;
      if(at>=f->menu_scroll+visible)f->menu_scroll=at-visible+1;
    }
    csd_menu_refresh(f);
  }
  return 1;
}
