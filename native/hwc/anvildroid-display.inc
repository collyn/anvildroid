/* Experimental source-level HWC extension. Include after do_hotplug() in
 * wayland-hwc.cpp. Context is the original HWC xdg listener_data (window*).
 * Call on the Wayland event thread, outside the bridge mutex, while window is
 * alive. No private structure offsets are exposed to the bridge.
 * Return 1=requested, 0=already large enough, negative errno=refused.
 * Neither successful return means SurfaceFlinger has acknowledged the mode.
 */
#include <cmath>
#include <cerrno>

extern "C" __attribute__((visibility("default")))
int anvildroid_hwc_grow_display_v1(void *context, int width, int height) {
    if (!context || width < 64 || height < 64 || width > 8192 || height > 8192)
        return -EINVAL;
    auto *window = static_cast<struct window *>(context);
    auto *d = window->display;
    if (!d || !std::isfinite(d->scale) || d->scale < 1 || d->scale > 16 ||
        d->width <= 0 || d->height <= 0)
        return -EINVAL;
    char property[PROPERTY_VALUE_MAX];
    /* Preserve explicit user configuration; padding requires its own coordinate
     * contract and is deliberately unsupported by this first prototype. */
    const char *fixed[] = {"persist.waydroid.width", "persist.waydroid.height",
                          "persist.waydroid.width_padding", "persist.waydroid.height_padding"};
    for (const char *name : fixed)
        if (property_get(name, property, nullptr) > 0)
            return -ENOTSUP;
    double current_width = std::floor(d->width * d->scale);
    double current_height = std::floor(d->height * d->scale);
    if (width <= current_width && height <= current_height)
        return 0;
    if (!d->procs || !d->procs->invalidate || !d->procs->hotplug)
        return -ENOTSUP;
    /* Grow each axis independently; never shrink capacity used by another app.
     * Round logical extent up, then validate the actual Android allocation. */
    double logical_width = std::fmax(d->width, std::ceil(width / d->scale));
    double logical_height = std::fmax(d->height, std::ceil(height / d->scale));
    double physical_width = std::floor(logical_width * d->scale);
    double physical_height = std::floor(logical_height * d->scale);
    if (physical_width > 8192 || physical_height > 8192 ||
        physical_width * physical_height > 16777216)
        return -E2BIG;
    choose_width_height(d, static_cast<int>(logical_width), static_cast<int>(logical_height));
    do_hotplug(d);
    return 1;
}
