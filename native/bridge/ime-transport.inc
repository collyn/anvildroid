/* Authenticated Android IME socket transport shared by desktop adapters. */
#ifndef ANVIL_IME_QUEUE_CAP
#define ANVIL_IME_QUEUE_CAP 64
#endif
static int ime_transfer(int fd, void *buffer, size_t size, int writing) {
  unsigned char *p = buffer;
  while (size) {
    long n = writing ? ime_socket_send(fd, p, size, 0x4000)
                     : recv(fd, p, size, 0);
    if (n <= 0) return 0;
    size -= (size_t)n; p += n;
  }
  return 1;
}
static int ime_write(int fd, char *text) {
  size_t n = strlen(text);
  unsigned char header[4] = {(unsigned char)(n >> 24), (unsigned char)(n >> 16),
                              (unsigned char)(n >> 8), (unsigned char)n};
  return ime_transfer(fd, header, 4, 1) && ime_transfer(fd, text, n, 1);
}
static int ime_open_socket(void) {
  int expected = -1;
  void *file = fopen("/data/system/packages.list", "r");
  if (!file) return -1;
  char line[2048], package[256]; int uid;
  while (fgets(line, sizeof(line), file))
    if (sscanf(line, "%255s %d", package, &uid) == 2 &&
        !strcmp(package, "org.anvildroid.ime")) { expected = uid; break; }
  fclose(file);
  if (expected < 10000) return -1;
  int fd = socket(1, 1 | 0x80000, 0);
  if (fd < 0) return -1;
  struct { unsigned short family; char path[108]; } address = { .family = 1 };
  const char *name = "org.anvildroid.ime.v1";
  memcpy(address.path + 1, name, strlen(name));
  struct { long seconds, microseconds; } timeout = {1, 0};
  setsockopt(fd, 1, 20, &timeout, sizeof(timeout));
  setsockopt(fd, 1, 21, &timeout, sizeof(timeout));
  struct { int pid; unsigned uid, gid; } peer;
  unsigned size = sizeof(peer);
  if (connect(fd, &address, (unsigned)(3 + strlen(name))) ||
      getsockopt(fd, 1, 17, &peer, &size) || peer.uid != (unsigned)expected) {
    close(fd); return -1;
  }
  return fd;
}
static void *ime_worker(void *unused) {
  (void)unused;
  /* Pipe consumers can close while a clipboard write is pending. Keep SIGPIPE
   * local to this worker; never change the Android process signal disposition. */
  extern int pthread_sigmask(int,const void *,void *);
  unsigned long pipe_signal=1ul<<12;
  pthread_sigmask(0,&pipe_signal,0);
  for (;;) {
    int fd = ime_open_socket();
    if (fd < 0) { usleep(500000); continue; }
    pthread_mutex_lock(mutex);
    ime_connected = 1; ime_active = ime_broken = 0;
#ifdef ANVIL_CLIPBOARD_ADAPTER
    ime_dirty = 1;
#endif
    ime_head = ime_tail = 0;
    pthread_mutex_unlock(mutex);
    __android_log_print(4, "AnvilDroidIme", "authenticated receiver connected");
    unsigned ticks = 0;
    const char *disconnect_reason = "socket transport";
    for (;;) {
      char value[32769];
      pthread_mutex_lock(mutex);
      int broken = ime_broken, queued = ime_head != ime_tail;
      if (queued) {
        memcpy(value, ime_queue[ime_head], sizeof(value));
        ime_head = (ime_head + 1) % ANVIL_IME_QUEUE_CAP;
      }
      pthread_mutex_unlock(mutex);
      if (broken) { disconnect_reason="text queue or protocol unavailable";break; }
      if (queued && !ime_write(fd, value)) break;
#ifdef ANVIL_CLIPBOARD_ADAPTER
      pthread_mutex_lock(mutex);
      int clipboard_queued=clipboard_next(value,sizeof(value));
      pthread_mutex_unlock(mutex);
      if(clipboard_queued&&!ime_write(fd,value))break;
#endif
      if (++ticks >= 40) {
        ticks = 0;
        char query[] = "{\"v\":1,\"op\":\"state\"}";
        if (!ime_write(fd, query)) break;
      }
      struct ime_pollfd event = {fd, 1, 0};
      int ready = poll(&event, 1, 50);
      if (ready < 0 || (event.revents & (8 | 16 | 32))) break;
      if (!(event.revents & 1)) continue;
      unsigned char header[4];
      if (!ime_transfer(fd, header, 4, 0)) break;
      uint32_t size = ((uint32_t)header[0] << 24) | ((uint32_t)header[1] << 16) |
                      ((uint32_t)header[2] << 8) | header[3];
      if (size < 2 || size > 32768 || !ime_transfer(fd, value, size, 0)) break;
      value[size] = 0;
      /* Parse only the authenticated receiver's generated metadata, never text. */
#ifdef ANVIL_CLIPBOARD_ADAPTER
      pthread_mutex_lock(mutex);
      int clipboard_status=clipboard_receive(value);
      pthread_mutex_unlock(mutex);
      if(clipboard_status<0){disconnect_reason="invalid clipboard frame";break;}
      if(clipboard_status)continue;
#endif
      if (strstr(value, "\"op\":\"state\"")) {
        char *g = strstr(value, "\"generation\":");
        char *p = strstr(value, "\"package\":\"");
        char *t = strstr(value, "\"inputType\":");
        unsigned long long generation;
        int type;
        if (!g || !p || !t || sscanf(g + 13, "%llu", &generation) != 1 ||
            sscanf(t + 12, "%d", &type) != 1) {disconnect_reason="invalid editor metadata";break;}
        p += 11;
        size_t length = 0;
        while (p[length] && p[length] != '"' && length < 255) ++length;
        if (p[length] != '"') {disconnect_reason="invalid editor package";break;}
        pthread_mutex_lock(mutex);
#ifdef ANVIL_CLIPBOARD_ADAPTER
        if(ime_generation!=generation || ime_type!=type ||
           ime_active!=(strstr(value,"\"active\":true")!=0) ||
           strlen(ime_package)!=length || memcmp(ime_package,p,length))ime_dirty=1;
#endif
        ime_generation = generation; ime_type = type;
        ime_active = strstr(value, "\"active\":true") != 0;
        memcpy(ime_package, p, length); ime_package[length] = 0;
#ifdef ANVIL_CLIPBOARD_ADAPTER
        char surrounding[4097]="";int cursor=0,anchor=0;
        char *encoded=strstr(value,"\"surrounding\":\"");
        char *cursor_value=strstr(value,"\"cursor\":");char *anchor_value=strstr(value,"\"anchor\":");
        if(encoded&&cursor_value&&anchor_value){
          encoded+=15;size_t n=0;while(encoded[n]&&encoded[n]!='"')++n;
          int size=anvil_b64_decode((unsigned char *)surrounding,4000,encoded,n),units;
          if(size<0 || sscanf(cursor_value+9,"%d",&cursor)!=1 || sscanf(anchor_value+9,"%d",&anchor)!=1 ||
             cursor<0||anchor<0||cursor>size||anchor>size){cursor=anchor=0;surrounding[0]=0;}
          else {surrounding[size]=0;if(!anvil_ime_utf16_index(surrounding,(size_t)size,&units)||
                  !anvil_ime_utf16_index(surrounding,(size_t)cursor,&units)||!anvil_ime_utf16_index(surrounding,(size_t)anchor,&units)){
                    cursor=anchor=0;surrounding[0]=0;
          }}
        }
        char *rx=strstr(value,"\"rectX\":");char *ry=strstr(value,"\"rectY\":");char *rh=strstr(value,"\"rectH\":");
        int rect_x=0,rect_y=0,rect_h=0;
        int valid=rx&&ry&&rh&&sscanf(rx+8,"%d",&rect_x)==1&&sscanf(ry+8,"%d",&rect_y)==1&&sscanf(rh+8,"%d",&rect_h)==1&&rect_h>0&&rect_h<=8192&&rect_x>=-65535&&rect_x<=65535&&rect_y>=-65535&&rect_y<=65535;
        if(strcmp(ime_surrounding,surrounding)||ime_cursor!=cursor||ime_anchor!=anchor||ime_rect_valid!=valid||ime_rect_x!=rect_x||ime_rect_y!=rect_y||ime_rect_h!=rect_h){
          memcpy(ime_surrounding,surrounding,strlen(surrounding)+1);ime_cursor=cursor;ime_anchor=anchor;
          ime_rect_valid=valid;ime_rect_x=rect_x;ime_rect_y=rect_y;ime_rect_h=rect_h;++ime_metadata_revision;ime_dirty=1;
        }
#endif
        pthread_mutex_unlock(mutex);
      } else if (strstr(value, "\"op\":\"result\"")) {
        /* Focus can change between a Wayland batch and Android's reply. Do not
         * tear down a healthy IME channel for an expected stale/unsupported
         * acknowledgement; the next state query will resynchronize it. */
        if (!strstr(value, "\"status\":\"ok\""))
          __android_log_print(5, "AnvilDroidIme", "Android batch acknowledgement was not applied");
      }
    }
    close(fd);
    pthread_mutex_lock(mutex);
    ime_connected = ime_active = 0;
    ime_head = ime_tail = 0;
#ifdef ANVIL_CLIPBOARD_ADAPTER
    ime_dirty = 1;
    free(clip_transmitting);clip_transmitting=0;clip_tx_stage=0;
    free(clip_receiving);clip_receiving=0;
#endif
    pthread_mutex_unlock(mutex);
    __android_log_print(4, "AnvilDroidIme", "receiver disconnected (%s)",disconnect_reason);
    usleep(500000);
  }
  return 0;
}
