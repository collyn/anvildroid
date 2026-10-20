/* SPDX-License-Identifier: MIT
 * Original bounded native keymap wire codec. No automatic file migration. */
#ifndef ANVIL_KEYMAP_SCHEMA_H
#define ANVIL_KEYMAP_SCHEMA_H
#define KEYMAP_V4_FIELDS 31
static int keymap_profile_row(const char **cursor,int values[KEYMAP_V4_FIELDS],int fields) {
  const char *s=*cursor;
  for(int i=0;i<fields;++i) {
    while(*s==' '||*s=='\t')++s;
    int value=0,digits=0;
    while(*s>='0'&&*s<='9') {
      if(++digits>15 || value>(65535-(*s-'0'))/10)return 0;
      value=value*10+(*s++-'0');
    }
    if(!digits||(*s!=' '&&*s!='\t'&&*s!='\r'&&*s!='\n'))return 0;
    values[i]=value;
  }
  while(*s==' '||*s=='\t')++s;
  if(*s=='\r')++s;
  if(*s++!='\n')return 0;
  *cursor=s;return 1;
}
static int keymap_profile_parse(const char *bytes,int length,const char *pkg,const char *runtime,struct keymap_model *m) {
  if(length<1||length>KEYMAP_PROFILE_LIMIT||bytes[length])return 0;
  for(int i=0;i<length;++i)if(!bytes[i])return 0;
  if(length<14||memcmp(bytes,"ANVIL_KEYMAP",12)||(bytes[12]!=' '&&bytes[12]!='\t'))return 0;
  int header_end=0;while(bytes[header_end]&&bytes[header_end]!='\n')++header_end;
  if(!bytes[header_end])return 0;
  for(int i=0;i<header_end;++i) {
    unsigned char c=(unsigned char)bytes[i];
    if(c=='\r'&&i==header_end-1)continue;
    if(c!='\t'&&(c<32||c>126))return 0;
  }
  char r[129],p[241],version[16],count_text[16];int used=0;
  if(sscanf(bytes,"ANVIL_KEYMAP %15s %128s %240s %15s%n",version,r,p,count_text,&used)!=4||
     !used||used>header_end||strcmp(r,runtime)||strcmp(p,pkg))return 0;
  int fields=!strcmp(version,"1")?4:!strcmp(version,"2")?9:!strcmp(version,"3")?19:!strcmp(version,"4")?31:0;
  int count=keymap_number(count_text,KEYMAP_POINTS);
  if(!fields||count<0)return 0;
  struct keymap_model parsed={.count=count,.selected=-1};const char *cursor=bytes+used;
  while(*cursor==' '||*cursor=='\t')++cursor;
  if(*cursor=='\r')++cursor;
  if(*cursor++!='\n')return 0;
  for(int i=0;i<count;++i) {
    int v[KEYMAP_V4_FIELDS]={0};if(!keymap_profile_row(&cursor,v,fields))return 0;
    if(fields<19&&v[4]>KEYMAP_AIM)return 0;
    if(fields==19&&v[4]>KEYMAP_LOOK)return 0;
    parsed.points[i]=(struct keymap_point){.key=v[0],.nx=v[1],.ny=v[2],.hold=v[3],.kind=v[4],
      .ex=v[5],.ey=v[6],.duration=v[7],.repeats=v[8],.source=v[9],.end_mode=v[10],.radius=v[11],
      .ramp_ms=v[12],.look_width=v[13],.look_height=v[14],.sensitivity_x=v[15],.sensitivity_y=v[16],
      .invert_y=v[17],.activation=v[18],.up_key=v[19],.left_key=v[20],.down_key=v[21],.right_key=v[22],
      .walk_key=v[23],.walk_radius=v[24],.suspend_key=v[25],.drag_shot=v[26],.skill_mode=v[27],
      .cancel_key=v[28],.cancel_x=v[29],.cancel_y=v[30]};
  }
  if(cursor-bytes!=length||!keymap_valid(&parsed,1))return 0;
  *m=parsed;return 1;
}
static int keymap_profile_format(char *data,size_t capacity,const char *pkg,const char *runtime,const struct keymap_model *m) {
  if(!keymap_valid(m,1))return 0;
  int used=snprintf(data,capacity,"ANVIL_KEYMAP 4 %s %s %d\n",runtime,pkg,m->count);
  if(used<0||(size_t)used>=capacity)return 0;
  for(int i=0;i<m->count;++i) {
    const struct keymap_point *p=&m->points[i];
    int n=snprintf(data+used,capacity-(size_t)used,
      "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
      p->key,p->nx,p->ny,p->hold,p->kind,p->ex,p->ey,p->duration,p->repeats,p->source,p->end_mode,p->radius,
      p->ramp_ms,p->look_width,p->look_height,p->sensitivity_x,p->sensitivity_y,p->invert_y,p->activation,
      p->up_key,p->left_key,p->down_key,p->right_key,p->walk_key,p->walk_radius,p->suspend_key,
      p->drag_shot,p->skill_mode,p->cancel_key,p->cancel_x,p->cancel_y);
    if(n<0||(size_t)n>=capacity-(size_t)used)return 0;
    used+=n;
  }
  return used<=KEYMAP_PROFILE_LIMIT?used:0;
}
#endif
