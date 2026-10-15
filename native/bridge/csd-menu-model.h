#ifndef ANVILDROID_CSD_MENU_MODEL_H
#define ANVILDROID_CSD_MENU_MODEL_H
#define CSD_MENU_ROW 32
#define CSD_MENU_PAD 4
#define CSD_MENU_WIDTH 200
#define CSD_SUBMENU_WIDTH 230
#define CSD_MENU_ROWS 3
/* Stable actions: keymap, Resize, its policies, Size, presets, orientations. */
enum { CSD_MENU_KEYMAP, CSD_MENU_RESIZE, CSD_MENU_LAYOUT, CSD_MENU_FIXED,
       CSD_MENU_SIZE, CSD_MENU_PRESET_FIRST, CSD_MENU_PRESET_LAST=8,
       CSD_MENU_LANDSCAPE, CSD_MENU_PORTRAIT, CSD_MENU_CUSTOM };
static int csd_menu_parent(int submenu) {return submenu==2?CSD_MENU_SIZE:CSD_MENU_RESIZE;}
static int csd_menu_first(int submenu) {return submenu==2?CSD_MENU_PRESET_FIRST:CSD_MENU_LAYOUT;}
static int csd_menu_count(int submenu) {return submenu==2?7:2;}
/* Narrow/short windows replace the root panel with a scrollable submenu. */
static int csd_menu_inline(int w,int h,int submenu) {
  int y=(submenu==2?2:1)*CSD_MENU_ROW;
  return w<CSD_MENU_WIDTH+CSD_SUBMENU_WIDTH||h<y+2*CSD_MENU_PAD+csd_menu_count(submenu)*CSD_MENU_ROW;
}
static int csd_menu_visible(int w,int h,int submenu) {
  int compact=csd_menu_inline(w,h,submenu);
  int y=compact?0:(submenu==2?2:1)*CSD_MENU_ROW;
  int rows=(h-y-2*CSD_MENU_PAD)/CSD_MENU_ROW-(compact?1:0);
  if(rows<1)rows=1;
  return rows<csd_menu_count(submenu)?rows:csd_menu_count(submenu);
}
static int csd_menu_panel_row(int x,int y,int w,int h,int px,int py,int width,int rows) {
  if(px+width>w)width=w-px;
  if(x<px+CSD_MENU_PAD||x>=px+width-CSD_MENU_PAD||y<py+CSD_MENU_PAD||
     y>=py+CSD_MENU_PAD+rows*CSD_MENU_ROW||y>=h)return -1;
  return (y-py-CSD_MENU_PAD)/CSD_MENU_ROW;
}
static int csd_menu_action(int submenu,int row,int compact,int scroll) {
  if(!submenu)return row==2?CSD_MENU_SIZE:row;
  if(compact&&row==0)return csd_menu_parent(submenu);
  return csd_menu_first(submenu)+row-(compact?1:0)+scroll;
}
static int csd_menu_row_at_offset(int x,int y,int w,int h,int submenu,int scroll) {
  if(submenu) {
    int compact=csd_menu_inline(w,h,submenu),visible=csd_menu_visible(w,h,submenu);
    int row=csd_menu_panel_row(x,y,w,h,compact?0:CSD_MENU_WIDTH,
      compact?0:(submenu==2?2:1)*CSD_MENU_ROW,CSD_SUBMENU_WIDTH,visible+compact);
    if(row>=0)return csd_menu_action(submenu,row,compact,scroll);
    if(compact)return -1;
  }
  int row=csd_menu_panel_row(x,y,w,h,0,0,CSD_MENU_WIDTH,CSD_MENU_ROWS);
  return row<0?-1:csd_menu_action(0,row,0,0);
}
static inline int csd_menu_row_at(int x,int y,int w,int h,int submenu) {
  return csd_menu_row_at_offset(x,y,w,h,submenu,0);
}
#endif
