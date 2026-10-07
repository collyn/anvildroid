/* Authenticated Android IME socket transport shared by desktop adapters. */
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
  for (;;) {
    int fd = ime_open_socket();
    if (fd < 0) { usleep(500000); continue; }
    pthread_mutex_lock(mutex);
    ime_connected = 1; ime_active = ime_broken = 0;
    ime_head = ime_tail = 0;
    pthread_mutex_unlock(mutex);
    __android_log_print(4, "AnvilDroidIme", "authenticated receiver connected");
    unsigned ticks = 0;
    for (;;) {
      char value[32769];
      pthread_mutex_lock(mutex);
      int broken = ime_broken, queued = ime_head != ime_tail;
      if (queued) {
        memcpy(value, ime_queue[ime_head], sizeof(value));
        ime_head = (ime_head + 1) % 16;
      }
      pthread_mutex_unlock(mutex);
      if (broken) break;
      if (queued && !ime_write(fd, value)) break;
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
      if (strstr(value, "\"op\":\"state\"")) {
        char *g = strstr(value, "\"generation\":");
        char *p = strstr(value, "\"package\":\"");
        char *t = strstr(value, "\"inputType\":");
        unsigned long long generation;
        int type;
        if (!g || !p || !t || sscanf(g + 13, "%llu", &generation) != 1 ||
            sscanf(t + 12, "%d", &type) != 1) break;
        p += 11;
        size_t length = 0;
        while (p[length] && p[length] != '"' && length < 255) ++length;
        if (p[length] != '"') break;
        pthread_mutex_lock(mutex);
        ime_generation = generation; ime_type = type;
        ime_active = strstr(value, "\"active\":true") != 0;
        memcpy(ime_package, p, length); ime_package[length] = 0;
        pthread_mutex_unlock(mutex);
      } else if (strstr(value, "\"op\":\"result\"") &&
                 !strstr(value, "\"status\":\"ok\"")) {
        /* Fail closed until a fresh authenticated connection resynchronizes. */
        break;
      }
    }
    close(fd);
    pthread_mutex_lock(mutex);
    ime_connected = ime_active = 0;
    ime_head = ime_tail = 0;
    pthread_mutex_unlock(mutex);
    __android_log_print(4, "AnvilDroidIme", "receiver disconnected");
    usleep(500000);
  }
  return 0;
}
