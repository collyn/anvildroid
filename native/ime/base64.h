/* SPDX-License-Identifier: MIT — bounded clipboard wire encoding. */
#ifndef ANVIL_BASE64_H
#define ANVIL_BASE64_H
static size_t anvil_b64_encode(char *out, const unsigned char *in, size_t n) {
  static const char digits[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t used = 0;
  for (size_t i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)in[i] << 16;
    if (i + 1 < n)
      v |= (uint32_t)in[i + 1] << 8;
    if (i + 2 < n)
      v |= in[i + 2];
    out[used++] = digits[v >> 18];
    out[used++] = digits[(v >> 12) & 63];
    out[used++] = i + 1 < n ? digits[(v >> 6) & 63] : '=';
    out[used++] = i + 2 < n ? digits[v & 63] : '=';
  }
  out[used] = 0;
  return used;
}
static int anvil_b64_decode(unsigned char *out, size_t capacity, const char *in,
                            size_t n) {
  if (n % 4)
    return -1;
  size_t used = 0;
  for (size_t i = 0; i < n; i += 4) {
    uint32_t v = 0;
    int pad = 0;
    for (int j = 0; j < 4; ++j) {
      unsigned char c = (unsigned char)in[i + j];
      int d;
      if (c >= 'A' && c <= 'Z')
        d = c - 'A';
      else if (c >= 'a' && c <= 'z')
        d = c - 'a' + 26;
      else if (c >= '0' && c <= '9')
        d = c - '0' + 52;
      else if (c == '+' || c == '-')
        d = 62;
      else if (c == '/' || c == '_')
        d = 63;
      else if (c == '=' && j >= 2 && i + 4 == n) {
        d = 0;
        ++pad;
      } else
        return -1;
      if (pad && c != '=')
        return -1;
      v = (v << 6) | (unsigned)d;
    }
    if (pad > 2 || (pad == 1 && (v & 255)) || (pad == 2 && (v & 65535)))
      return -1;
    for (int j = 0; j < 3 - pad; ++j) {
      if (used >= capacity)
        return -1;
      out[used++] = (unsigned char)(v >> (16 - j * 8));
    }
  }
  return (int)used;
}
#endif
