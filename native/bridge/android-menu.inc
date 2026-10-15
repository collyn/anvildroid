/* Size requests update Android first; presentation follows its acknowledged
 * physical projection without hotplugging the HWC. */
#include "menu-font.h"
#include "csd-size-model.h"
enum { CANVAS_MENU_ROW=32, CANVAS_MENU_WIDTH=200, CANVAS_MENU_SUBWIDTH=230,
       CANVAS_MENU_OPTIONS=7 };
static struct {struct wl_proxy *surface,*subsurface;int width,height,attached;} canvas_menu;
static int canvas_menu_open,canvas_menu_submenu,canvas_menu_hover=-1,canvas_menu_pressed=-2;
static int canvas_menu_focus,canvas_menu_offset,canvas_size_pending;
static uint32_t canvas_menu_blocked[24];
static int canvas_menu_compact(void) {return host_w<CANVAS_MENU_WIDTH+CANVAS_MENU_SUBWIDTH;}
static int canvas_menu_visible(void) {
  int rows=(host_h-8)/CANVAS_MENU_ROW-(canvas_menu_compact()?1:0);
  if(rows<1)rows=1;
  return rows<CANVAS_MENU_OPTIONS?rows:CANVAS_MENU_OPTIONS;
}
/* Action 0 opens/goes back; 1..4 presets, 5/6 orientations, 7 Custom. */
static int canvas_menu_row(int x,int y) {
  int row=(y-4)/CANVAS_MENU_ROW;
  if(y<4||y>=host_h)return -1;
  if(canvas_menu_submenu) {
    int compact=canvas_menu_compact(),px=compact?0:CANVAS_MENU_WIDTH;
    int right=px+CANVAS_MENU_SUBWIDTH;if(right>host_w)right=host_w;
    if(x>=px+4&&x<right-4&&row<canvas_menu_visible()+compact) {
      if(compact&&!row)return 0;
      return row+1-compact+canvas_menu_offset;
    }
    if(compact)return -1;
  }
  int right=host_w<CANVAS_MENU_WIDTH?host_w:CANVAS_MENU_WIDTH;
  return x>=4&&x<right-4&&row==0?0:-1;
}
static void canvas_menu_panel(uint32_t *pixels,int w,int h,int px,int submenu) {
  int compact=submenu&&canvas_menu_compact();
  int width=submenu?CANVAS_MENU_SUBWIDTH:CANVAS_MENU_WIDTH;
  if(px+width>w)width=w-px;
  int rows=submenu?canvas_menu_visible()+compact:1,bottom=8+rows*CANVAS_MENU_ROW;
  for(int y=0;y<h&&y<bottom;++y)for(int x=px;x<px+width;++x) {
    int action=canvas_menu_row(x,y);
    pixels[(size_t)y*w+x]=(x==px||x==px+width-1||!y||y==bottom-1)?0xff454545:
      action>=0&&action==canvas_menu_hover?0xff414141:0xff282828;
  }
  for(int row=0;row<rows;++row) {
    int action=submenu?(compact&&!row?0:row+1-compact+canvas_menu_offset):0;
    int baseline=24+row*CANVAS_MENU_ROW,selected=0;
    const char *label="Size";
    if(action>=1&&action<=4) {
      label=csd_menu_sizes[action-1].label;
      int w=display_projection.lw?display_projection.lw:host_w,h=display_projection.lh?display_projection.lh:host_h;
      selected=!canvas_custom_size.enabled&&w==csd_menu_sizes[action-1].width&&h==csd_menu_sizes[action-1].height;
    } else if(action==7) {label="Custom";selected=canvas_custom_size.enabled;
    } else if(action>=5) {
      label=action==5?"Landscape":"Portrait";
      int w=display_projection.lw?display_projection.lw:host_w,h=display_projection.lh?display_projection.lh:host_h;
      selected=action==5?w>h:h>w;
    }
    menu_text(pixels,w,h,px+width-8,px+12,baseline,action?selected?"*":"":compact?"<":"",0xffdedede,14);
    menu_text(pixels,w,h,px+width-8,px+(action||compact?30:12),baseline,label,0xffdedede,14);
    const char *hint=!action&&!compact?">":action&&row==rows-1&&
      canvas_menu_offset+canvas_menu_visible()<CANVAS_MENU_OPTIONS?"v":
      action&&row==compact&&canvas_menu_offset?"^":"";
    menu_text(pixels,w,h,px+width-8,px+width-24,baseline,hint,0xffdedede,14);
  }
}
static void canvas_menu_paint(uint32_t *pixels,int w,int h) {
  int limit=CANVAS_MENU_OPTIONS-canvas_menu_visible();
  if(canvas_menu_offset>limit)canvas_menu_offset=limit;
  if(canvas_menu_submenu&&canvas_menu_compact())canvas_menu_panel(pixels,w,h,0,1);
  else {
    canvas_menu_panel(pixels,w,h,0,0);
    if(canvas_menu_submenu)canvas_menu_panel(pixels,w,h,CANVAS_MENU_WIDTH,1);
  }
}
static void canvas_menu_sync(void) {
  if(!csd_visible())canvas_menu_open=0;
  if(!canvas_menu_open) {
    if(canvas_menu.surface&&canvas_menu.attached) {
      union wl_argument empty[3]={{.o=NULL},{.i=0},{.i=0}};
      marshal(canvas_menu.surface,1,NULL,1,0,empty);marshal(canvas_menu.surface,6,NULL,1,0,NULL);
      canvas_menu.width=canvas_menu.height=0;
      canvas_menu.attached=0;
    }
    return;
  }
  if(!csd_compositor||!csd_subcompositor||!csd_shm)return;
  if(!canvas_menu.surface) {
    union wl_argument id={.o=NULL};
    canvas_menu.surface=marshal(csd_compositor,0,csd_surface_i,1,0,&id);
    if(!canvas_menu.surface)return;
    union wl_argument sub[3]={{.o=NULL},{.o=canvas_menu.surface},{.o=root_surface}};
    canvas_menu.subsurface=marshal(csd_subcompositor,1,csd_subsurface_i,1,0,sub);
    if(!canvas_menu.subsurface) {
      marshal(canvas_menu.surface,0,NULL,1,1,NULL);canvas_menu.surface=NULL;return;
    }
  }
  /* New Android children must stay below the transparent input cover. */
  union wl_argument above={.o=canvas_menu.surface};
  for(struct tracked *o=objects;o;o=o->next)
    if(o->kind==SUBSURFACE&&o->parent==root_surface)marshal(o->proxy,3,NULL,1,0,&above);
  if(canvas_menu.width!=host_w||canvas_menu.height!=host_h) {
    struct wl_proxy *buffer=csd_paint(4,host_w,host_h,maximized);
    if(!buffer){canvas_menu_open=0;canvas_menu_sync();return;}
    union wl_argument attach[3]={{.o=buffer},{.i=0},{.i=0}};
    union wl_argument damage[4]={{.i=0},{.i=0},{.i=host_w},{.i=host_h}};
    marshal(canvas_menu.surface,1,NULL,1,0,attach);marshal(canvas_menu.surface,2,NULL,1,0,damage);
    canvas_menu.width=host_w;canvas_menu.height=host_h;
    canvas_menu.attached=1;
  }
  marshal(canvas_menu.surface,6,NULL,1,0,NULL);
}
static void canvas_menu_refresh(void) {
  canvas_menu.width=canvas_menu.height=0;canvas_menu_sync();
  if(root_surface)marshal(root_surface,6,NULL,get_version(root_surface),0,NULL);
}
static void canvas_menu_close(void) {
  canvas_menu_open=canvas_menu_submenu=canvas_menu_offset=0;
  canvas_menu_sync();
  if(root_surface)marshal(root_surface,6,NULL,get_version(root_surface),0,NULL);
}
static void canvas_menu_toggle(void) {
  if(canvas_menu_open){canvas_menu_close();return;}
  canvas_menu_open=1;canvas_menu_submenu=canvas_menu_offset=0;
  canvas_menu_hover=0;canvas_menu_pressed=-2;canvas_menu_refresh();
}
static void canvas_menu_destroy(void) {
  if(canvas_menu.subsurface)marshal(canvas_menu.subsurface,0,NULL,1,1,NULL);
  if(canvas_menu.surface)marshal(canvas_menu.surface,0,NULL,1,1,NULL);
  canvas_menu.surface=canvas_menu.subsurface=NULL;canvas_menu.width=canvas_menu.height=canvas_menu.attached=0;
  canvas_menu_open=canvas_menu_submenu=canvas_menu_focus=canvas_menu_offset=canvas_size_pending=0;
}
static void canvas_menu_activate(int action) {
  if(action<0){canvas_menu_close();return;}
  if(!action) {
    canvas_menu_submenu=!canvas_menu_submenu;canvas_menu_offset=0;
    canvas_menu_hover=canvas_menu_submenu?1:0;canvas_menu_pressed=-2;canvas_menu_refresh();return;
  }
  if(!canvas_menu_submenu||action>CANVAS_MENU_OPTIONS)return;
  if(action==7) {
    canvas_custom_size.enabled=1;
    custom_size_queue(&canvas_custom_size,host_w,host_h,0);
    canvas_menu_close();display_frame_ticks=0;display_frame_schedule();return;
  }
  canvas_custom_size=(struct custom_size){0};
  int w=display_projection.lw?display_projection.lw:host_w;
  int h=display_projection.lh?display_projection.lh:host_h;
  if(action<=4){w=csd_menu_sizes[action-1].width;h=csd_menu_sizes[action-1].height;}
  else if((action==5&&w<h)||(action==6&&w>h)){int swap=w;w=h;h=swap;}
  if(!display_size_request(w,h,0))return;
  restore_w=w;restore_h=h;
  if(maximized){canvas_size_pending=1;marshal(top,10,NULL,get_version(top),0,NULL);}
  maximized=0;canvas_menu_close(); /* Wait for Android acknowledgement before fitting. */
  display_frame_ticks=0;display_frame_schedule();
}
static void canvas_menu_motion(void) {
  int row=canvas_menu_row(csd_x,csd_y);
  if(row!=canvas_menu_hover){canvas_menu_hover=row;canvas_menu_refresh();}
}
static void canvas_menu_button(uint32_t button,uint32_t state) {
  if(!canvas_menu_open||button!=0x110)return;
  int row=canvas_menu_row(csd_x,csd_y);
  if(state)canvas_menu_pressed=row;
  else {if(row==canvas_menu_pressed)canvas_menu_activate(row);canvas_menu_pressed=-2;}
}
static void canvas_menu_scroll(int step) {
  if(!canvas_menu_open||!canvas_menu_submenu)return;
  int next=canvas_menu_offset+step,limit=CANVAS_MENU_OPTIONS-canvas_menu_visible();
  if(next<0)next=0;
  if(next>limit)next=limit;
  if(next!=canvas_menu_offset){canvas_menu_offset=next;canvas_menu_pressed=-2;canvas_menu_hover=canvas_menu_row(csd_x,csd_y);canvas_menu_refresh();}
}
static int canvas_menu_key(uint32_t key,uint32_t state) {
  if(key>=768)return 0;
  uint32_t mask=1u<<(key%32);
  if(!state&&(canvas_menu_blocked[key/32]&mask)){canvas_menu_blocked[key/32]&=~mask;return 1;}
  if(!canvas_menu_open||!focused)return 0;
  if(!state)return 1;
  canvas_menu_blocked[key/32]|=mask;
  if(key==1||key==105){if(canvas_menu_submenu)canvas_menu_activate(0);else if(key==1)canvas_menu_close();}
  else if(key==106&&!canvas_menu_submenu)canvas_menu_activate(0);
  else if(key==28)canvas_menu_activate(canvas_menu_hover);
  else if(key==103||key==108) {
    if(!canvas_menu_submenu)canvas_menu_hover=0;
    else {
      int at=canvas_menu_hover-1;
      if(at<0||at>=CANVAS_MENU_OPTIONS)at=key==103?CANVAS_MENU_OPTIONS-1:0;
      else at=(at+(key==103?CANVAS_MENU_OPTIONS-1:1))%CANVAS_MENU_OPTIONS;
      canvas_menu_hover=at+1;
      if(at<canvas_menu_offset)canvas_menu_offset=at;
      if(at>=canvas_menu_offset+canvas_menu_visible())canvas_menu_offset=at-canvas_menu_visible()+1;
    }
    canvas_menu_refresh();
  }
  return 1;
}
