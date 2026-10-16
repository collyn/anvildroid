/* SPDX-License-Identifier: MIT — XIM offsets are Unicode characters, v3 offsets
 * bytes. */
#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>
namespace x11host {
inline std::u32string decode_utf8(const std::string &s) {
  std::u32string out;
  for (size_t i = 0; i < s.size();) {
    unsigned char a = s[i++];
    uint32_t cp;
    unsigned n;
    if (a < 128) {
      cp = a;
      n = 0;
    } else if (a >= 0xc2 && a <= 0xdf) {
      cp = a & 31;
      n = 1;
    } else if (a >= 0xe0 && a <= 0xef) {
      cp = a & 15;
      n = 2;
    } else if (a >= 0xf0 && a <= 0xf4) {
      cp = a & 7;
      n = 3;
    } else
      throw std::runtime_error("Invalid UTF-8");
    if (i + n > s.size())
      throw std::runtime_error("Truncated UTF-8");
    for (unsigned j = 0; j < n; ++j) {
      unsigned char b = s[i++];
      if ((b & 0xc0) != 0x80)
        throw std::runtime_error("Invalid UTF-8 continuation");
      cp = (cp << 6) | (b & 63);
    }
    if ((n == 1 && cp < 128) || (n == 2 && cp < 0x800) ||
        (n == 3 && cp < 0x10000) || cp > 0x10ffff ||
        (cp >= 0xd800 && cp <= 0xdfff))
      throw std::runtime_error("Invalid Unicode scalar");
    out.push_back(cp);
  }
  return out;
}
inline std::string encode_utf8(const std::u32string &s) {
  std::string out;
  for (uint32_t cp : s) {
    if (cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff))
      throw std::runtime_error("Invalid Unicode scalar");
    if (cp < 128)
      out += char(cp);
    else if (cp < 0x800) {
      out += char(0xc0 | (cp >> 6));
      out += char(0x80 | (cp & 63));
    } else if (cp < 0x10000) {
      out += char(0xe0 | (cp >> 12));
      out += char(0x80 | ((cp >> 6) & 63));
      out += char(0x80 | (cp & 63));
    } else {
      out += char(0xf0 | (cp >> 18));
      out += char(0x80 | ((cp >> 12) & 63));
      out += char(0x80 | ((cp >> 6) & 63));
      out += char(0x80 | (cp & 63));
    }
  }
  return out;
}
struct Preedit {
  std::u32string text;
  int caret = 0;
  void replace(int first, int length, const std::u32string &value, int cursor) {
    if (first < 0 || length < 0 || size_t(first) > text.size() ||
        size_t(length) > text.size() - first)
      throw std::runtime_error("Invalid XIM preedit range");
    auto next = text;
    next.replace(first, length, value);
    if (encode_utf8(next).size() > 4000)
      throw std::runtime_error("XIM preedit too large");
    text = std::move(next);
    caret = std::clamp(cursor, 0, int(text.size()));
  }
  int byte_caret() const {
    return int(encode_utf8(text.substr(0, caret)).size());
  }
};
} // namespace x11host
