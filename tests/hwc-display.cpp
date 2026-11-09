// Compile the actual extension against a minimal HWC model, not a second
// implementation. This validates policy only, NOT Android hotplug/ABI.
#include <cassert>
#include <cstring>
#include <initializer_list>
#define PROPERTY_VALUE_MAX 92
struct callbacks { void (*invalidate)(); void (*hotplug)(); };
struct display { double scale; int width, height; callbacks *procs; };
struct window { struct display *display; };
static int requests;
static const char *fixed;
static int property_get(const char *name, char *, const char *) {
    return fixed && !std::strcmp(fixed, name) ? 1 : 0;
}
static void choose_width_height(display *d, int w, int h) { d->width=w; d->height=h; }
static void do_hotplug(display *) { ++requests; }
static void callback() {}
#include "../native/hwc/anvildroid-display.inc"
int main() {
    callbacks procs{callback, callback};
    display d{1, 1920, 1080, &procs}; window w{&d};
    assert(anvildroid_hwc_grow_display_v1(nullptr, 2400, 1400) == -EINVAL);
    assert(anvildroid_hwc_grow_display_v1(&w, 2400, 1400) == 1);
    assert(d.width == 2400 && d.height == 1400 && requests == 1);
    assert(anvildroid_hwc_grow_display_v1(&w, 800, 600) == 0 && requests == 1);
    assert(anvildroid_hwc_grow_display_v1(&w, 2500, 800) == 1);
    assert(d.width == 2500 && d.height == 1400);
    assert(anvildroid_hwc_grow_display_v1(&w, 8192, 8192) == -E2BIG);
    assert(d.width == 2500 && d.height == 1400);
    fixed="persist.waydroid.width";
    assert(anvildroid_hwc_grow_display_v1(&w, 2600, 1500) == -ENOTSUP);
    fixed="persist.waydroid.height_padding";
    assert(anvildroid_hwc_grow_display_v1(&w, 2600, 1500) == -ENOTSUP);
    fixed=nullptr; d.procs=nullptr;
    assert(anvildroid_hwc_grow_display_v1(&w, 2600, 1500) == -ENOTSUP);
    d.procs=&procs;
    for (double scale : {1.0, 1.25, 1.5, 2.0}) {
        d={scale, 800, 600, &procs};
        assert(anvildroid_hwc_grow_display_v1(&w, 2401, 1401) == 1);
        assert(std::floor(d.width * scale) >= 2401);
        assert(std::floor(d.height * scale) >= 1401);
        assert(d.scale == scale);
    }
}
