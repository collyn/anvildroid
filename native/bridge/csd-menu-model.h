#ifndef ANVILDROID_CSD_MENU_MODEL_H
#define ANVILDROID_CSD_MENU_MODEL_H
#define CSD_MENU_ROW 32
#define CSD_MENU_PAD 4
#define CSD_MENU_WIDTH 200
#define CSD_SUBMENU_WIDTH 230
#define CSD_MENU_ROWS 2
/* Use a side panel when it fits; narrow windows show Resize with a back
 * action in the same panel so neither options nor input leave the window. */
static int csd_menu_inline(int w,int h) {
  return w<CSD_MENU_WIDTH+CSD_SUBMENU_WIDTH||h<2*CSD_MENU_PAD+3*CSD_MENU_ROW;
}
static int csd_menu_panel_row(int x,int y,int w,int h,int px,int py,int width,int rows) {
  if(px+width>w)width=w-px;
  if(x<px+CSD_MENU_PAD||x>=px+width-CSD_MENU_PAD||y<py+CSD_MENU_PAD||
     y>=py+CSD_MENU_PAD+rows*CSD_MENU_ROW||y>=h)return -1;
  return (y-py-CSD_MENU_PAD)/CSD_MENU_ROW;
}
/* Action IDs: 0 keymap, 1 open/back Resize, 2 layout, 3 fixed resolution. */
static int csd_menu_row_at(int x,int y,int w,int h,int submenu) {
  if(submenu&&csd_menu_inline(w,h)) {
    int row=csd_menu_panel_row(x,y,w,h,0,0,CSD_SUBMENU_WIDTH,3);
    return row<0?-1:row+1;
  }
  if(submenu) {
    int row=csd_menu_panel_row(x,y,w,h,CSD_MENU_WIDTH,CSD_MENU_ROW,CSD_SUBMENU_WIDTH,2);
    if(row>=0)return row+2;
  }
  return csd_menu_panel_row(x,y,w,h,0,0,CSD_MENU_WIDTH,2);
}
#endif
