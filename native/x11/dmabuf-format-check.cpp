/* SPDX-License-Identifier: MIT — DMA-BUF plane layout validation without a
 * display or GPU. Layouts mirror what hwcomposer.waydroid submits (a single
 * plane-0 `add`) for minigbm and gbm_gralloc allocations. */
#include "dmabuf-format.hpp"
#include <cassert>
#include <cstdio>
#include <map>
using namespace x11host;

static std::map<int, int64_t> sizes;
static const DmaSize size_of = [](int fd) -> int64_t {
  auto it = sizes.find(fd);
  return it == sizes.end() ? -1 : it->second;
};
static bool all(uint32_t) { return true; }
using Planes = std::array<DmaPlane, dma_max_planes>;
static Planes one(int fd, uint32_t offset, uint32_t stride,
                  uint64_t modifier = 0) {
  Planes p;
  p[0] = {fd, offset, stride, modifier, true};
  return p;
}
static DmaCheck check(uint32_t format, int w, int h, const Planes &p,
                      uint32_t flags = 0,
                      std::function<bool(uint32_t)> advertised = all) {
  return dma_check(format, w, h, flags, p, advertised, size_of);
}
static void expect(const DmaCheck &c, DmaStatus status) {
  if (c.status != status)
    fprintf(stderr, "status=%d expected=%d reason=%s\n", int(c.status),
            int(status), c.reason.c_str());
  assert(c.status == status);
}

int main() {
  // minigbm YV12 (HAL_PIXEL_FORMAT_YV12 -> '9997'), gbm_mesa spoofed R8
  // allocation: stride = width (not 32-aligned), ceil chroma geometry.
  {
    const int w = 854, h = 481;
    const uint32_t cs = 427, ch = 241;
    sizes[3] = int64_t(w) * h + 2 * int64_t(cs) * ch;
    auto c = check(drm::YVU420_ANDROID, w, h, one(3, 0, w));
    expect(c, DmaStatus::ok);
    assert(c.layout.import == drm::YVU420 && c.layout.planes == 3 &&
           c.layout.yuv);
    assert(c.layout.fd[1] == 3 && c.layout.fd[2] == 3);
    assert(c.layout.offset[1] == uint32_t(w * h) && c.layout.stride[1] == cs);
    assert(c.layout.offset[2] == uint32_t(w * h) + cs * ch &&
           c.layout.stride[2] == cs);
    sizes[3] -= 1; // Last chroma byte outside the allocation.
    expect(check(drm::YVU420_ANDROID, w, h, one(3, 0, w)),
           DmaStatus::unsupported);
    // Its import format must be importable; '9997' itself is never listed.
    expect(check(drm::YVU420_ANDROID, w, h, one(3, 0, w), 0,
                 [](uint32_t f) { return f != drm::YVU420; }),
           DmaStatus::unsupported);
    assert(!dma_advertisable(*dma_format(drm::YVU420_ANDROID)));
  }
  // gbm_gralloc/Android YV12: chroma stride ALIGN(stride / 2, 16), h / 2.
  {
    const int w = 1280, h = 720;
    const uint32_t s = 1344, cs = 672;
    sizes[4] = int64_t(s) * (h + h / 2);
    expect(check(drm::YVU420, w, h, one(4, 64, s)), DmaStatus::unsupported);
    sizes[4] += 64;
    auto c = check(drm::YVU420, w, h, one(4, 64, s));
    expect(c, DmaStatus::ok);
    assert(c.layout.offset[1] == 64 + s * h && c.layout.stride[1] == cs);
    assert(c.layout.offset[2] == 64 + s * h + cs * (h / 2));
    auto odd = check(drm::YVU420, w, h, one(4, 0, 1288));
    expect(odd, DmaStatus::ok);
    assert(odd.layout.stride[1] == 656); // ALIGN(644, 16)
  }
  // NV12/NV21/P010 single allocation: CbCr at stride * h, same stride.
  {
    const int w = 1920, h = 1080;
    sizes[5] = int64_t(w) * h * 3 / 2;
    for (uint32_t f : {drm::NV12, drm::NV21}) {
      auto c = check(f, w, h, one(5, 0, w));
      expect(c, DmaStatus::ok);
      assert(c.layout.planes == 2 && c.layout.offset[1] == uint32_t(w * h) &&
             c.layout.stride[1] == uint32_t(w) && c.layout.fd[1] == 5);
    }
    expect(check(drm::NV12, w, h, one(5, 1, w)), DmaStatus::unsupported);
    expect(check(drm::P010, w, h, one(5, 0, w * 2)), DmaStatus::unsupported);
    sizes[6] = int64_t(w) * 2 * h * 3 / 2;
    expect(check(drm::P010, w, h, one(6, 0, w * 2)), DmaStatus::ok);
    expect(check(drm::P010, w, h, one(6, 0, w)), DmaStatus::unsupported);
  }
  // Explicit planes in separate fds; each bounded by its own fd.
  {
    sizes[7] = 64 * 48;
    sizes[8] = 32 * 24;
    sizes[9] = 32 * 24;
    Planes p;
    p[0] = {7, 0, 64, 0, true};
    p[1] = {8, 0, 32, 0, true};
    p[2] = {9, 0, 32, 0, true};
    auto c = check(drm::YUV420, 64, 48, p);
    expect(c, DmaStatus::ok);
    assert(c.layout.fd[1] == 8 && c.layout.fd[2] == 9 &&
           c.layout.stride[2] == 32);
    p[2].offset = 1;
    expect(check(drm::YUV420, 64, 48, p), DmaStatus::unsupported);
    p[2].offset = 0;
    p[1].stride = 31;
    expect(check(drm::YUV420, 64, 48, p), DmaStatus::unsupported);
    p[1].stride = 32;
    p[2].modifier = 1;
    expect(check(drm::YUV420, 64, 48, p), DmaStatus::unsupported);
    p[2].modifier = 0;
    // Structural errors stay fatal.
    expect(check(drm::NV12, 64, 48, p), DmaStatus::incomplete);
    Planes two = p;
    two[2] = {};
    expect(check(drm::YUV420, 64, 48, two), DmaStatus::incomplete);
    expect(check(drm::XRGB8888, 16, 12, two), DmaStatus::incomplete);
    Planes gap = p;
    gap[1] = {};
    expect(check(drm::YUV420, 64, 48, gap), DmaStatus::incomplete);
    Planes none;
    expect(check(drm::XRGB8888, 16, 12, none), DmaStatus::incomplete);
    sizes[10] = 64 * 48 * 3 / 2;
    Planes nv;
    nv[0] = {10, 0, 64, 0, true};
    nv[1] = {10, 64 * 48, 64, 0, true};
    expect(check(drm::NV12, 64, 48, nv), DmaStatus::ok);
  }
  // Packed RGB: per-format bytes per pixel.
  {
    sizes[11] = 100 * 10 * 4;
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 400)), DmaStatus::ok);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 399)),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(11, 4, 400)),
           DmaStatus::unsupported);
    // Row tail: the last row needs only width * cpp bytes.
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 440)),
           DmaStatus::unsupported);
    sizes[11] = 440 * 9 + 400;
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 440)), DmaStatus::ok);
    expect(check(drm::RGB565, 100, 10, one(11, 0, 200)), DmaStatus::ok);
    expect(check(drm::RGB565, 100, 10, one(11, 0, 199)),
           DmaStatus::unsupported);
    expect(check(drm::BGR888, 100, 10, one(11, 0, 300)), DmaStatus::ok);
    expect(check(drm::BGR888, 100, 10, one(11, 0, 299)),
           DmaStatus::unsupported);
    expect(check(drm::ABGR16161616F, 100, 4, one(11, 0, 800)), DmaStatus::ok);
    // Modifiers: LINEAR and implicit only.
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 400, drm::MOD_INVALID)),
           DmaStatus::ok);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 400, 0x0100000000000001)),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 400), 1),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(12, 0, 400)),
           DmaStatus::unsupported); // unknown fd size
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 400), 0,
                 [](uint32_t) { return false; }),
           DmaStatus::unsupported);
    expect(check(fourcc('G', 'R', '8', '8'), 100, 10, one(11, 0, 400)),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0x80000000u, 400)),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, dma_max_stride + 4)),
           DmaStatus::unsupported);
    expect(check(drm::XRGB8888, 100, 10, one(11, 0, 0)),
           DmaStatus::unsupported);
  }
  // Overflow-shaped requests cannot wrap the bounds arithmetic.
  {
    sizes[13] = int64_t(1) << 31;
    expect(check(drm::NV12, 8192, 2048, one(13, 0x7fffffff, dma_max_stride)),
           DmaStatus::unsupported);
    expect(check(drm::YVU420_ANDROID, 8192, 2048,
                 one(13, 0x7fff0000, dma_max_stride)),
           DmaStatus::unsupported);
  }
  // Dimensions are fatal and checked first.
  for (auto [w, h] :
       {std::pair{0, 1}, {1, 0}, {-1, 5}, {8193, 1}, {1, 8193}, {8192, 2049}})
    expect(check(drm::XRGB8888, w, h, one(11, 0, 400)),
           DmaStatus::bad_dimensions);
  // Advertisement table: every advertisable entry is its own import format;
  // planar formats used by Android video are present.
  for (uint32_t f : {drm::YVU420, drm::YUV420, drm::NV12, drm::NV21, drm::P010,
                     drm::RGB565, drm::BGR888})
    assert(dma_format(f) && dma_advertisable(*dma_format(f)));
  assert(fourcc_name(drm::NV12) == "NV12" &&
         fourcc_name(drm::YVU420_ANDROID) == "9997");
  assert(!dma_bt709(720, 576) && dma_bt709(1280, 720) && dma_bt709(720, 578));
  puts("PASS DMA-BUF plane layouts, implied YUV planes, bounds and fatality");
  return 0;
}
