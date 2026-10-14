#ifndef ANVILDROID_CSD_CURSOR_H
#define ANVILDROID_CSD_CURSOR_H
/* Small neutral arrow, premultiplied ARGB for a real Wayland cursor surface.
 * Four coverage samples keep the outline legible on dark/light app content. */
static uint32_t csd_cursor_pixel(int x,int y) {
  static const int points[7][2]={{3,2},{3,24},{8,19},{12,29},{17,27},{12,17},{21,17}};
  unsigned coverage=0,white=0;
  for(int sy=1;sy<4;sy+=2)for(int sx=1;sx<4;sx+=2) {
    int px=x*4+sx,py=y*4+sy,inside=0,edge=0;
    for(int i=0,j=6;i<7;j=i++) {
      int ax=points[j][0]*4,ay=points[j][1]*4;
      int bx=points[i][0]*4,by=points[i][1]*4;
      if((ay>py)!=(by>py)) {
        int cross=ax+(py-ay)*(bx-ax)/(by-ay);
        if(px<cross)inside=!inside;
      }
      int dx=bx-ax,dy=by-ay,length=dx*dx+dy*dy;
      int dot=(px-ax)*dx+(py-ay)*dy;
      if(dot<0)dot=0;
      if(dot>length)dot=length;
      int ex=(px-ax)*length-dot*dx,ey=(py-ay)*length-dot*dy;
      if((int64_t)ex*ex+(int64_t)ey*ey<=(int64_t)16*length*length)edge=1;
    }
    if(inside){++coverage;if(!edge)++white;}
  }
  unsigned channel=(white*245+(coverage-white)*24)/4;
  return (coverage*255/4<<24)|(channel<<16)|(channel<<8)|channel;
}
#endif
