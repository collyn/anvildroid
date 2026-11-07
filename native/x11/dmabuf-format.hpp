/* SPDX-License-Identifier: MIT — guest DMA-BUF format table and plane layout
 * validation, independent of EGL so it can be checked without a display.
 *
 * Waydroid's composer (hwcomposer.waydroid) always sends exactly one `add`
 * (plane 0) per buffer, even for planar YUV video. minigbm and gbm_gralloc
 * allocate those images as one contiguous linear allocation, so the chroma
 * planes are reconstructed here from the documented allocator layout. Every
 * plane, explicit or implied, is bounds-checked against its fd's size before
 * any import.
 */
#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace x11host {
constexpr uint32_t fourcc(char a, char b, char c, char d) {
  return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 |
         uint32_t(uint8_t(c)) << 16 | uint32_t(uint8_t(d)) << 24;
}
namespace drm {
constexpr uint32_t ARGB8888 = fourcc('A', 'R', '2', '4'),
                   XRGB8888 = fourcc('X', 'R', '2', '4'),
                   ABGR8888 = fourcc('A', 'B', '2', '4'),
                   XBGR8888 = fourcc('X', 'B', '2', '4'),
                   RGB565 = fourcc('R', 'G', '1', '6'),
                   BGR565 = fourcc('B', 'G', '1', '6'),
                   RGB888 = fourcc('R', 'G', '2', '4'),
                   BGR888 = fourcc('B', 'G', '2', '4'),
                   ARGB2101010 = fourcc('A', 'R', '3', '0'),
                   XRGB2101010 = fourcc('X', 'R', '3', '0'),
                   ABGR2101010 = fourcc('A', 'B', '3', '0'),
                   XBGR2101010 = fourcc('X', 'B', '3', '0'),
                   ABGR16161616F = fourcc('A', 'B', '4', 'H'),
                   XBGR16161616F = fourcc('X', 'B', '4', 'H'),
                   YUV420 = fourcc('Y', 'U', '1', '2'),
                   YVU420 = fourcc('Y', 'V', '1', '2'),
                   NV12 = fourcc('N', 'V', '1', '2'),
                   NV21 = fourcc('N', 'V', '2', '1'),
                   P010 = fourcc('P', '0', '1', '0'),
                   // minigbm's private fourcc for HAL_PIXEL_FORMAT_YV12. The
                   // cros composer path forwards it verbatim.
    YVU420_ANDROID = fourcc('9', '9', '9', '7');
constexpr uint64_t MOD_LINEAR = 0, MOD_INVALID = 0x00ffffffffffffffull;
} // namespace drm

enum class Implied {
  none,     // single-plane format, or only explicit planes accepted
  biplanar, // NV12/NV21/P010: CbCr at stride*h with the luma stride
  android,  // YV12 per <system/graphics.h>/gbm_gralloc: ALIGN(s/2,16), h/2
  minigbm,  // minigbm YVU420_ANDROID: ceil(s/2) and ceil(h/2)
};
struct DmaFormat {
  uint32_t fourcc; // accepted from the guest
  uint32_t import; // EGL_LINUX_DRM_FOURCC_EXT
  int planes;
  uint8_t hsub[4], vsub[4], cpp[4];
  bool yuv;
  Implied implied;
};
// Advertisement order: the existing 8888 formats first.
inline const std::vector<DmaFormat> &dma_formats() {
  using namespace drm;
  // clang-format off
  static const std::vector<DmaFormat> table = {
      {ARGB8888, ARGB8888, 1, {1}, {1}, {4}, false, Implied::none},
      {XRGB8888, XRGB8888, 1, {1}, {1}, {4}, false, Implied::none},
      {ABGR8888, ABGR8888, 1, {1}, {1}, {4}, false, Implied::none},
      {XBGR8888, XBGR8888, 1, {1}, {1}, {4}, false, Implied::none},
      {RGB565, RGB565, 1, {1}, {1}, {2}, false, Implied::none},
      {BGR565, BGR565, 1, {1}, {1}, {2}, false, Implied::none},
      {RGB888, RGB888, 1, {1}, {1}, {3}, false, Implied::none},
      {BGR888, BGR888, 1, {1}, {1}, {3}, false, Implied::none},
      {ARGB2101010, ARGB2101010, 1, {1}, {1}, {4}, false, Implied::none},
      {XRGB2101010, XRGB2101010, 1, {1}, {1}, {4}, false, Implied::none},
      {ABGR2101010, ABGR2101010, 1, {1}, {1}, {4}, false, Implied::none},
      {XBGR2101010, XBGR2101010, 1, {1}, {1}, {4}, false, Implied::none},
      {ABGR16161616F, ABGR16161616F, 1, {1}, {1}, {8}, false, Implied::none},
      {XBGR16161616F, XBGR16161616F, 1, {1}, {1}, {8}, false, Implied::none},
      {YVU420, YVU420, 3, {1, 2, 2}, {1, 2, 2}, {1, 1, 1}, true,
       Implied::android},
      {YUV420, YUV420, 3, {1, 2, 2}, {1, 2, 2}, {1, 1, 1}, true,
       Implied::android},
      {NV12, NV12, 2, {1, 2}, {1, 2}, {1, 2}, true, Implied::biplanar},
      {NV21, NV21, 2, {1, 2}, {1, 2}, {1, 2}, true, Implied::biplanar},
      {P010, P010, 2, {1, 2}, {1, 2}, {2, 4}, true, Implied::biplanar},
      // Never advertised: accepted when its import format is.
      {YVU420_ANDROID, YVU420, 3, {1, 2, 2}, {1, 2, 2}, {1, 1, 1}, true,
       Implied::minigbm},
  };
  // clang-format on
  return table;
}
inline const DmaFormat *dma_format(uint32_t fourcc) {
  for (auto &f : dma_formats())
    if (f.fourcc == fourcc)
      return &f;
  return nullptr;
}
inline bool dma_advertisable(const DmaFormat &f) {
  return f.fourcc == f.import;
}
inline std::string fourcc_name(uint32_t f) {
  std::string s;
  for (int i = 0; i < 4; ++i) {
    char c = char((f >> (8 * i)) & 0xff);
    s += (c >= 0x20 && c < 0x7f) ? c : '?';
  }
  return s;
}

constexpr int dma_max_planes = 4;
constexpr int32_t dma_max_extent = 8192;
constexpr uint64_t dma_max_area = 16777216;
constexpr uint32_t dma_max_stride = 131072;
struct DmaPlane {
  int fd = -1;
  uint32_t offset = 0, stride = 0;
  uint64_t modifier = 0;
  bool set = false;
};
struct DmaLayout {
  uint32_t import = 0;
  int planes = 0;
  bool yuv = false;
  int fd[dma_max_planes]{-1, -1, -1, -1};
  uint32_t offset[dma_max_planes]{}, stride[dma_max_planes]{};
};
enum class DmaStatus {
  ok,
  // Fatal protocol misuse: the request is structurally invalid.
  incomplete,
  bad_dimensions,
  // Non-fatal: the request is well formed but cannot be imported here.
  unsupported,
};
struct DmaCheck {
  DmaStatus status = DmaStatus::ok;
  std::string reason;
  DmaLayout layout;
};
// Size of the allocation behind fd, or <0 when unknown.
using DmaSize = std::function<int64_t(int fd)>;

inline uint32_t div_up(uint32_t v, uint32_t d) { return (v + d - 1) / d; }

/* Validate a create/create_immed request. `advertised` reports whether an
 * import fourcc is importable here. Structural errors are reported as fatal
 * statuses. Everything else (unknown format, modifier, flags, short stride,
 * bounds) is `unsupported`: no plane is ever imported unless every row of
 * every plane lies inside its fd. */
inline DmaCheck dma_check(uint32_t format, int32_t w, int32_t h, uint32_t flags,
                          const std::array<DmaPlane, dma_max_planes> &planes,
                          const std::function<bool(uint32_t)> &advertised,
                          const DmaSize &size_of) {
  DmaCheck r;
  auto fail = [&](DmaStatus s, std::string why) {
    r.status = s;
    r.reason = std::move(why);
    return r;
  };
  if (w < 1 || h < 1 || w > dma_max_extent || h > dma_max_extent ||
      uint64_t(w) * uint64_t(h) > dma_max_area)
    return fail(DmaStatus::bad_dimensions, "buffer dimensions out of range");
  if (!planes[0].set)
    return fail(DmaStatus::incomplete, "plane 0 missing");
  int given = 0;
  while (given < dma_max_planes && planes[given].set)
    ++given;
  for (int i = given; i < dma_max_planes; ++i)
    if (planes[i].set)
      return fail(DmaStatus::incomplete, "non-contiguous planes");
  const DmaFormat *f = dma_format(format);
  if (f && given > f->planes)
    return fail(DmaStatus::incomplete, "too many planes for format");
  if (f && given > 1 && given < f->planes)
    return fail(DmaStatus::incomplete, "missing planes for format");
  if (!f)
    return fail(DmaStatus::unsupported, "unknown format");
  if (!advertised(f->import))
    return fail(DmaStatus::unsupported, "format not importable by host EGL");
  if (flags)
    return fail(DmaStatus::unsupported, "unsupported buffer flags");
  for (int i = 0; i < given; ++i) {
    if (planes[i].modifier != planes[0].modifier)
      return fail(DmaStatus::unsupported, "mixed plane modifiers");
    if (planes[i].modifier != drm::MOD_LINEAR &&
        planes[i].modifier != drm::MOD_INVALID)
      return fail(DmaStatus::unsupported, "non-LINEAR modifier");
    if (planes[i].offset > uint32_t(INT32_MAX) ||
        planes[i].stride > dma_max_stride || !planes[i].stride)
      return fail(DmaStatus::unsupported, "plane offset/stride out of range");
  }
  DmaLayout &l = r.layout;
  l.import = f->import;
  l.planes = f->planes;
  l.yuv = f->yuv;
  for (int i = 0; i < given; ++i) {
    l.fd[i] = planes[i].fd;
    l.offset[i] = planes[i].offset;
    l.stride[i] = planes[i].stride;
  }
  if (given < f->planes) {
    // Single `add` for a planar image: reconstruct the allocator's
    // contiguous layout behind plane 0.
    uint64_t s = l.stride[0], o = l.offset[0];
    uint64_t c_stride = 0, next = o + s * uint64_t(h), c_rows = 0;
    switch (f->implied) {
    case Implied::biplanar:
      c_stride = s;
      break;
    case Implied::android:
      c_stride = ((s / 2) + 15) & ~uint64_t(15);
      c_rows = uint64_t(h) / 2;
      break;
    case Implied::minigbm:
      c_stride = (s + 1) / 2;
      c_rows = div_up(uint32_t(h), 2);
      break;
    case Implied::none:
      return fail(DmaStatus::unsupported, "format requires explicit planes");
    }
    for (int i = 1; i < f->planes; ++i) {
      if (next > uint32_t(INT32_MAX) || c_stride > dma_max_stride)
        return fail(DmaStatus::unsupported, "implied plane out of range");
      l.fd[i] = l.fd[0];
      l.offset[i] = uint32_t(next);
      l.stride[i] = uint32_t(c_stride);
      next += c_stride * c_rows;
    }
  }
  for (int i = 0; i < l.planes; ++i) {
    uint64_t row = uint64_t(div_up(uint32_t(w), f->hsub[i])) * f->cpp[i];
    uint64_t rows = div_up(uint32_t(h), f->vsub[i]);
    if (l.stride[i] < row)
      return fail(DmaStatus::unsupported, "plane stride below row size");
    int64_t size = size_of(l.fd[i]);
    uint64_t end = uint64_t(l.offset[i]) + (rows - 1) * l.stride[i] + row;
    if (size < 0 || end > uint64_t(size))
      return fail(DmaStatus::unsupported, "plane exceeds allocation");
  }
  return r;
}
// BT.709 for HD content, BT.601 otherwise (the composer conveys no
// dataspace); both limited range, as produced by Android video decoders.
inline bool dma_bt709(int32_t w, int32_t h) { return w >= 1280 || h > 576; }
} // namespace x11host
