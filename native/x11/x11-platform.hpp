/* SPDX-License-Identifier: MIT — native windows and GPU surface composition.
 * The guest bridge supplies every decoration/menu/overlay as ordinary surfaces.
 */
#pragma once
#include "dmabuf-format.hpp"
#include "output-mode.hpp"
#include "output-scale.hpp"
#include "surface-tree.hpp"
#include "window-frame.hpp"
#define EGL_EGLEXT_PROTOTYPES
#define GL_GLEXT_PROTOTYPES
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <X11/XKBlib.h>
#include <X11/Xatom.h>
#include <X11/Xcursor/Xcursor.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XInput2.h>
#include <X11/extensions/Xfixes.h>
#include <X11/extensions/Xrandr.h>
#include <X11/extensions/scrnsaver.h>
#include <X11/extensions/shape.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <functional>
#include <tuple>
#include <set>
#include <string>
#include <unistd.h>

namespace x11host {
inline void ensure(bool ok, const char *message) {
  if (!ok)
    throw std::runtime_error(message);
}
// Context token: GL objects that outlive the Platform skip GL cleanup (the
// context, and with it every object, is already gone).
struct GlContext {
  EGLDisplay egl = EGL_NO_DISPLAY;
  EGLSurface scratch = EGL_NO_SURFACE;
  EGLContext context = EGL_NO_CONTEXT;
  bool current() const {
    return eglMakeCurrent(egl, scratch, scratch, context) == EGL_TRUE;
  }
};
// Snapshot textures are recycled by size: allocating a new one per frame
// made the kernel allocate and zero a fresh GPU buffer every frame.
struct TexturePool {
  static constexpr size_t limit = 4;
  std::vector<std::tuple<int, int, GLuint>> spare;
};
struct GpuImage : Image {
  std::weak_ptr<TexturePool> pool;
  std::weak_ptr<GlContext> gl;
  ~GpuImage() override {
    if (!texture)
      return;
    if (auto shared = pool.lock(); shared && shared->spare.size() < TexturePool::limit) {
      shared->spare.emplace_back(width, height, texture);
      return;
    }
    if (auto context = gl.lock(); context && context->current())
      glDeleteTextures(1, &texture);
  }
};
// One guest DMA-BUF imported once. Android re-renders into the same few
// allocations, so later frames sample the cached EGLImage.
struct DmaImport {
  std::weak_ptr<GlContext> gl;
  EGLImageKHR image = EGL_NO_IMAGE_KHR;
  GLuint texture = 0;
  PFNEGLDESTROYIMAGEKHRPROC destroy = nullptr;
  ~DmaImport() {
    auto context = gl.lock();
    if (!context || !context->current())
      return;
    if (texture)
      glDeleteTextures(1, &texture);
    if (image != EGL_NO_IMAGE_KHR && destroy)
      destroy(context->egl, image);
  }
};
struct NativeWindow {
  Window window = 0;
  Colormap colormap = 0;
  EGLSurface surface = EGL_NO_SURFACE;
  // Logical (Wayland) size, and the X11 drawable size in physical pixels.
  // They differ only while the desktop scale is not 1.
  int width = 640, height = 480, pw = 640, ph = 480;
  bool mapped = false;
  bool swap_interval_set = false;
  // Remembered position requested before the first map (physical root px).
  bool position_hint = false;
  int hint_x = 0, hint_y = 0;
};
class Platform {
  bool headless;
  EGLDisplay egl = EGL_NO_DISPLAY;
  EGLConfig config{};
  EGLContext context = EGL_NO_CONTEXT;
  EGLSurface scratch = EGL_NO_SURFACE;
  GLuint program = 0, external_program = 0;
  PFNEGLCREATEIMAGEKHRPROC create_image = nullptr;
  PFNEGLDESTROYIMAGEKHRPROC destroy_image = nullptr;
  PFNGLEGLIMAGETARGETTEXTURE2DOESPROC bind_image = nullptr;
  // EGL_ANDROID_native_fence_sync: release guest buffers when the GPU copy
  // signals instead of blocking the event loop in glFinish().
  PFNEGLCREATESYNCKHRPROC create_sync = nullptr;
  PFNEGLDESTROYSYNCKHRPROC destroy_sync = nullptr;
  PFNEGLDUPNATIVEFENCEFDANDROIDPROC dup_fence = nullptr;
  GLint pos = 0, uv = 0;
  unsigned fake_id = 1;
  std::shared_ptr<GlContext> gl = std::make_shared<GlContext>();
  std::shared_ptr<TexturePool> pool = std::make_shared<TexturePool>();
  GLuint copy_fbo = 0;
  struct Locations { GLint pos = -1, uv = -1, image = -1; };
  Locations program_locations, external_locations;
  // Import fourccs this EGL accepts as LINEAR (or implicit) DMA-BUFs.
  std::vector<uint32_t> importable;
  std::set<std::string> reported;
  void query_dmabuf_formats() {
    const char *ext = eglQueryString(egl, EGL_EXTENSIONS);
    std::string extensions = ext ? ext : "";
    auto has = [&](const std::string &name) {
      return (" " + extensions + " ").find(" " + name + " ") !=
             std::string::npos;
    };
    auto query = reinterpret_cast<PFNEGLQUERYDMABUFFORMATSEXTPROC>(
        eglGetProcAddress("eglQueryDmaBufFormatsEXT"));
    auto modifiers = reinterpret_cast<PFNEGLQUERYDMABUFMODIFIERSEXTPROC>(
        eglGetProcAddress("eglQueryDmaBufModifiersEXT"));
    EGLint count = 0;
    if (!has("EGL_EXT_image_dma_buf_import_modifiers") || !query ||
        !modifiers || !query(egl, 0, nullptr, &count) || count <= 0) {
      // Without the query, keep the formats every importer has accepted.
      importable = {drm::ARGB8888, drm::XRGB8888, drm::ABGR8888,
                    drm::XBGR8888};
      return;
    }
    std::vector<EGLint> listed(count);
    if (!query(egl, count, listed.data(), &count))
      count = 0;
    listed.resize(std::max<EGLint>(count, 0));
    for (auto &f : dma_formats()) {
      if (!dma_advertisable(f) ||
          std::find(listed.begin(), listed.end(), EGLint(f.import)) ==
              listed.end())
        continue;
      EGLint n = 0;
      if (!modifiers(egl, EGLint(f.import), 0, nullptr, nullptr, &n))
        continue;
      bool linear = n == 0; // Implicit-only importers take LINEAR.
      if (n > 0) {
        std::vector<EGLuint64KHR> mods(n);
        std::vector<EGLBoolean> external(n);
        if (modifiers(egl, EGLint(f.import), n, mods.data(), external.data(),
                      &n))
          for (EGLint i = 0; i < n; ++i)
            linear |= mods[i] == drm::MOD_LINEAR;
      }
      if (linear)
        importable.push_back(f.import);
    }
  }
  XIMCallback im_destroy{};
  static void im_instantiate(Display *, XPointer data, XPointer) {
    reinterpret_cast<Platform *>(data)->open_im();
  }
  static GLuint shader(GLenum type, const char *src) {
    auto s = glCreateShader(type);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    ensure(ok, "Cannot compile native X11 surface shader");
    return s;
  }
  static GLuint make_program(bool external) {
    const char *v =
        "attribute vec2 position;attribute vec2 texcoord;varying vec2 uv;void "
        "main(){gl_Position=vec4(position,0.,1.);uv=texcoord;}";
    const char *f =
        external ? "#extension GL_OES_EGL_image_external : require\nprecision "
                   "mediump float;varying vec2 uv;uniform samplerExternalOES "
                   "image;void main(){gl_FragColor=texture2D(image,uv);}"
                 : "precision mediump float;varying vec2 uv;uniform sampler2D "
                   "image;void main(){gl_FragColor=texture2D(image,uv);}";
    auto a = shader(GL_VERTEX_SHADER, v), b = shader(GL_FRAGMENT_SHADER, f),
         p = glCreateProgram();
    glAttachShader(p, a);
    glAttachShader(p, b);
    glLinkProgram(p);
    glDeleteShader(a);
    glDeleteShader(b);
    GLint ok = 0;
    glGetProgramiv(p, GL_LINK_STATUS, &ok);
    ensure(ok, "Cannot link native X11 surface shader");
    return p;
  }
  void quad(GLuint texture, bool external, const float *vertices,
            const float *coords) {
    auto p = external ? external_program : program;
    auto &where = external ? external_locations : program_locations;
    glUseProgram(p);
    if (where.pos < 0) {
      where.pos = glGetAttribLocation(p, "position");
      where.uv = glGetAttribLocation(p, "texcoord");
      where.image = glGetUniformLocation(p, "image");
    }
    pos = where.pos;
    uv = where.uv;
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(external ? GL_TEXTURE_EXTERNAL_OES : GL_TEXTURE_2D, texture);
    glUniform1i(where.image, 0);
    glEnableVertexAttribArray(pos);
    glEnableVertexAttribArray(uv);
    glVertexAttribPointer(pos, 2, GL_FLOAT, GL_FALSE, 0, vertices);
    glVertexAttribPointer(uv, 2, GL_FLOAT, GL_FALSE, 0, coords);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glDisableVertexAttribArray(pos);
    glDisableVertexAttribArray(uv);
  }
  static void parameters(GLenum target) {
    glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(target, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(target, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  }
  std::shared_ptr<Image> image(int w, int h) {
    if (headless) {
      auto i = std::make_shared<Image>();
      i->width = w;
      i->height = h;
      return i;
    }
    auto i = std::make_shared<GpuImage>();
    i->width = w;
    i->height = h;
    i->pool = pool;
    i->gl = gl;
    auto &spare = pool->spare;
    for (auto it = spare.begin(); it != spare.end(); ++it)
      if (std::get<0>(*it) == w && std::get<1>(*it) == h) {
        i->texture = std::get<2>(*it);
        spare.erase(it);
        return i;
      }
    // A different size: drop spares rather than growing the pool unbounded.
    if (spare.size() >= TexturePool::limit) {
      glDeleteTextures(1, &std::get<2>(spare.front()));
      spare.erase(spare.begin());
    }
    glGenTextures(1, &i->texture);
    glBindTexture(GL_TEXTURE_2D, i->texture);
    parameters(GL_TEXTURE_2D);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE,
                 nullptr);
    return i;
  }

public:
  Display *display = nullptr;
  Atom protocols = 0, wm_delete = 0;
  XIM xim = nullptr;
  // Reports an opened IM (true) or a dead server (false). On false, Xlib has
  // already invalidated the IM and every XIC; they must not be destroyed.
  std::function<void(bool)> im_changed;
  bool im_retry = false;
  int xi_opcode = 0;
  bool touch_available = false, constraints_available = false;
  bool randr_available = false, randr_monitors = false;
  int randr_event = 0;
  // Effective desktop scale in 1/120 units (see output-scale.hpp). Wayland
  // coordinates are logical; X11 coordinates are logical x scale.
  int scale120 = scale_unit;
  double physical(double logical) const { return to_physical(logical, scale120); }
  double logical(double physical) const { return to_logical(physical, scale120); }
  int physical_size(int logical) const { return to_physical_size(logical, scale120); }
  int logical_size(int physical) const { return to_logical_size(physical, scale120); }
  bool saver_suspend = false;
  explicit Platform(bool test = false) : headless(test) {
    if (test)
      return;
    XInitThreads();
    display = XOpenDisplay(nullptr);
    ensure(display, "Cannot connect to the selected X11 desktop");
    XSetErrorHandler([](Display *, XErrorEvent *event) -> int {
      // X11 windows can disappear while asynchronous selection/grab work is in
      // flight.
      if (event->error_code != BadWindow && event->error_code != BadDrawable)
        fprintf(
            stderr,
            "X11 protocol error code=%d request=%d minor=%d resource=0x%lx\n",
            event->error_code, event->request_code, event->minor_code,
            event->resourceid);
      return 0;
    });
    int first_event, first_error;
    if (XQueryExtension(display, "XInputExtension", &xi_opcode, &first_event,
                        &first_error)) {
      int major = 2, minor = 2;
      if (XIQueryVersion(display, &major, &minor) != Success || major < 2)
        xi_opcode = 0;
      else {
        touch_available = major > 2 || minor >= 2;
        unsigned char bits[XIMaskLen(XI_LASTEVENT)]{};
        XISetMask(bits, XI_RawMotion);
        XIEventMask mask{XIAllMasterDevices, int(sizeof(bits)), bits};
        XISelectEvents(display, DefaultRootWindow(display), &mask, 1);
        memset(bits, 0, sizeof(bits));
        XISetMask(bits, XI_HierarchyChanged);
        mask.deviceid = XIAllDevices;
        XISelectEvents(display, DefaultRootWindow(display), &mask, 1);
      }
    }
    int fixes_event, fixes_error, shape_event, shape_error;
    int fixes_major = 5, fixes_minor = 0, shape_major = 0, shape_minor = 0;
    constraints_available =
        xi_opcode &&
        XFixesQueryExtension(display, &fixes_event, &fixes_error) &&
        XFixesQueryVersion(display, &fixes_major, &fixes_minor) &&
        fixes_major >= 2 &&
        XShapeQueryExtension(display, &shape_event, &shape_error) &&
        XShapeQueryVersion(display, &shape_major, &shape_minor) &&
        (shape_major > 1 || (shape_major == 1 && shape_minor >= 1));
    int randr_event, randr_error, randr_major = 1, randr_minor = 3;
    randr_available =
        XRRQueryExtension(display, &randr_event, &randr_error) &&
        XRRQueryVersion(display, &randr_major, &randr_minor) &&
        (randr_major > 1 || randr_minor >= 3);
    // RandR 1.5 monitors include `xrandr --setmonitor` virtual monitors.
    randr_monitors = randr_available && (randr_major > 1 || randr_minor >= 5);
    if (randr_available)
      XRRSelectInput(display, DefaultRootWindow(display),
                     RRScreenChangeNotifyMask | RRCrtcChangeNotifyMask |
                         RROutputChangeNotifyMask);
    else
      randr_event = 0;
    // Root ConfigureNotify also reports monitor (setmonitor) and screen size
    // changes; PropertyNotify reports RESOURCE_MANAGER (Xft.dpi) updates.
    XSelectInput(display, DefaultRootWindow(display),
                 StructureNotifyMask | PropertyChangeMask);
    int saver_event, saver_error, saver_major = 0, saver_minor = 0;
    saver_suspend =
        XScreenSaverQueryExtension(display, &saver_event, &saver_error) &&
        XScreenSaverQueryVersion(display, &saver_major, &saver_minor) &&
        (saver_major > 1 || (saver_major == 1 && saver_minor >= 1));
    Bool repeat;
    XkbSetDetectableAutoRepeat(display, True, &repeat);
    egl = eglGetDisplay((EGLNativeDisplayType)display);
    EGLint major, minor;
    ensure(eglInitialize(egl, &major, &minor),
           "Cannot initialize X11 EGL presentation");
    EGLint attributes[] = {EGL_SURFACE_TYPE,
                           EGL_WINDOW_BIT | EGL_PBUFFER_BIT,
                           EGL_RENDERABLE_TYPE,
                           EGL_OPENGL_ES2_BIT,
                           EGL_RED_SIZE,
                           8,
                           EGL_GREEN_SIZE,
                           8,
                           EGL_BLUE_SIZE,
                           8,
                           EGL_ALPHA_SIZE,
                           8,
                           EGL_NONE},
           n = 0;
    ensure(eglChooseConfig(egl, attributes, nullptr, 0, &n) && n,
           "No ARGB X11 EGL configuration");
    std::vector<EGLConfig> configs(n);
    ensure(eglChooseConfig(egl, attributes, configs.data(), n, &n),
           "Cannot enumerate ARGB X11 EGL configurations");
    bool found = false;
    for (int i = 0; i < n; ++i) {
      EGLint visual_id = 0;
      eglGetConfigAttrib(egl, configs[i], EGL_NATIVE_VISUAL_ID, &visual_id);
      XVisualInfo wanted{};
      wanted.visualid = visual_id;
      wanted.screen = DefaultScreen(display);
      int count = 0;
      auto *visual = XGetVisualInfo(display, VisualIDMask | VisualScreenMask,
                                    &wanted, &count);
      // An EGL alpha channel alone can still use an opaque depth-24 visual.
      found = visual && count && visual->depth == 32 &&
              visual->c_class == TrueColor;
      if (visual)
        XFree(visual);
      if (found) {
        config = configs[i];
        break;
      }
    }
    ensure(found, "No depth-32 ARGB X11 visual for transparent guest CSD");
    EGLint ctx[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    eglBindAPI(EGL_OPENGL_ES_API);
    context = eglCreateContext(egl, config, EGL_NO_CONTEXT, ctx);
    ensure(context != EGL_NO_CONTEXT, "Cannot create X11 EGL context");
    EGLint pb[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    scratch = eglCreatePbufferSurface(egl, config, pb);
    current();
    create_image = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(
        eglGetProcAddress("eglCreateImageKHR"));
    destroy_image = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(
        eglGetProcAddress("eglDestroyImageKHR"));
    bind_image = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(
        eglGetProcAddress("glEGLImageTargetTexture2DOES"));
    ensure(create_image && destroy_image && bind_image,
           "Missing EGL DMA-BUF image support");
    program = make_program(false);
    external_program = make_program(true);
    *gl = GlContext{egl, scratch, context};
    query_dmabuf_formats();
    if (const char *extensions = eglQueryString(egl, EGL_EXTENSIONS);
        extensions && std::strstr(extensions, "EGL_ANDROID_native_fence_sync") &&
        !getenv("ANVILDROID_X11_SYNC_COPY")) {
      create_sync = reinterpret_cast<PFNEGLCREATESYNCKHRPROC>(eglGetProcAddress("eglCreateSyncKHR"));
      destroy_sync = reinterpret_cast<PFNEGLDESTROYSYNCKHRPROC>(eglGetProcAddress("eglDestroySyncKHR"));
      dup_fence = reinterpret_cast<PFNEGLDUPNATIVEFENCEFDANDROIDPROC>(eglGetProcAddress("eglDupNativeFenceFDANDROID"));
      if (!create_sync || !destroy_sync || !dup_fence)
        create_sync = nullptr;
    }
    printf("x11_adapter_release=%s\n", create_sync ? "fence" : "finish");
    protocols = atom("WM_PROTOCOLS");
    wm_delete = atom("WM_DELETE_WINDOW");
    XSetLocaleModifiers("");
    // Xlib invokes this now when an IM is available, and again on XIM_SERVERS
    // after a server it opened has died. A later server can thus attach even
    // when none was running at startup, with the same pinned XMODIFIERS.
    XRegisterIMInstantiateCallback(display, nullptr, nullptr, nullptr,
                                   im_instantiate,
                                   reinterpret_cast<XPointer>(this));
    printf("x11_adapter_gpu=%s\n", glGetString(GL_RENDERER));
    std::string names;
    for (auto f : importable)
      names += (names.empty() ? "" : ",") + fourcc_name(f);
    printf("x11_adapter_dmabuf_formats=%s\n", names.c_str());
    printf("x11_output_refresh_mhz=%d advertised=%d\n", output_refresh_mhz(),
           advertised_refresh_mhz(output_refresh_mhz()));
  }
  ~Platform() {
    if (headless)
      return;
    XUnregisterIMInstantiateCallback(display, nullptr, nullptr, nullptr,
                                     im_instantiate,
                                     reinterpret_cast<XPointer>(this));
    if (xim)
      XCloseIM(xim);
    current();
    for (auto &spare : pool->spare)
      glDeleteTextures(1, &std::get<2>(spare));
    pool->spare.clear();
    if (copy_fbo)
      glDeleteFramebuffers(1, &copy_fbo);
    gl.reset(); // objects released later find no context and skip GL calls
    glDeleteProgram(program);
    glDeleteProgram(external_program);
    eglMakeCurrent(egl, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(egl, scratch);
    eglDestroyContext(egl, context);
    eglTerminate(egl);
    XCloseDisplay(display);
  }
  bool testing() const { return headless; }
  // Headless checks have no importer; they validate the full table.
  bool dmabuf_importable(uint32_t import) const {
    if (headless)
      return true;
    return std::find(importable.begin(), importable.end(), import) !=
           importable.end();
  }
  std::vector<uint32_t> dmabuf_formats() const {
    std::vector<uint32_t> out;
    for (auto &f : dma_formats())
      if (dma_advertisable(f) && dmabuf_importable(f.import))
        out.push_back(f.fourcc);
    return out;
  }
  // Opaque black stand-in for content that cannot be imported. It keeps the
  // guest's surface size and lets the composer continue instead of dying.
  std::shared_ptr<Image> placeholder(int w, int h) {
    current();
    auto i = image(w, h);
    if (headless)
      return i;
    GLuint fbo;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                           i->texture, 0);
    ensure(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
           "Cannot cache Android surface");
    glViewport(0, 0, w, h);
    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    auto error = glGetError();
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDeleteFramebuffers(1, &fbo);
    ensure(error == GL_NO_ERROR, "Cannot create placeholder surface");
    return i;
  }
  void open_im() {
    im_retry = false;
    if (headless || xim)
      return;
    xim = XOpenIM(display, nullptr, nullptr, nullptr);
    if (!xim)
      return;
    im_destroy = {reinterpret_cast<XPointer>(this),
                  [](XIM, XPointer data, XPointer) {
                    reinterpret_cast<Platform *>(data)->im_destroyed();
                  }};
    XSetIMValues(xim, XNDestroyCallback, &im_destroy, nullptr);
    if (getenv("ANVIL_TEST_XIM_TRACE"))
      fprintf(stderr, "xim opened\n");
    if (im_changed)
      im_changed(true);
  }
  void im_destroyed() {
    // Runs inside Xlib's server teardown, which frees the IM afterwards.
    xim = nullptr;
    // A replacing server may already own the selection; retry once outside
    // Xlib instead of waiting for an XIM_SERVERS change that may have passed.
    im_retry = true;
    if (getenv("ANVIL_TEST_XIM_TRACE"))
      fprintf(stderr, "xim server destroyed\n");
    if (im_changed)
      im_changed(false);
  }
  std::pair<int, int> output_size() const {
    return headless
               ? std::make_pair(1920, 1080)
               : std::make_pair(DisplayWidth(display, DefaultScreen(display)),
                                DisplayHeight(display, DefaultScreen(display)));
  }
  // Xft.dpi from the root RESOURCE_MANAGER string (xrdb), 0 when unset.
  double resource_dpi() const {
    if (headless)
      return 0;
    Atom type;
    int format;
    unsigned long count, left;
    unsigned char *bytes = nullptr;
    double dpi = 0;
    if (XGetWindowProperty(display, DefaultRootWindow(display),
                           XInternAtom(display, "RESOURCE_MANAGER", False), 0,
                           262144, False, XA_STRING, &type, &format, &count,
                           &left, &bytes) == Success &&
        bytes) {
      if (type == XA_STRING && format == 8)
        dpi = x11host::resource_dpi(
            std::string(reinterpret_cast<char *>(bytes), count));
      XFree(bytes);
    }
    return dpi;
  }
  int desktop_scale120() const {
    return scale120_from_dpi(resource_dpi());
  }
  // One entry per RandR 1.5 monitor (virtual monitors included), else per
  // active CRTC, else the whole screen. Physical pixels, root coordinates.
  std::vector<Monitor> monitors() const {
    std::vector<Monitor> list;
    if (headless) {
      Monitor m;
      m.name = "X11";
      m.width = 1920;
      m.height = 1080;
      m.mm_width = estimated_mm(m.width);
      m.mm_height = estimated_mm(m.height);
      m.primary = true;
      list.push_back(m);
      return list;
    }
    auto modes = output_modes();
    int32_t fallback = select_refresh_mhz(modes);
    Window root = DefaultRootWindow(display);
    if (randr_monitors) {
      int count = 0;
      XRRMonitorInfo *info = XRRGetMonitors(display, root, True, &count);
      XRRScreenResources *resources =
          info ? XRRGetScreenResourcesCurrent(display, root) : nullptr;
      for (int i = 0; info && i < count; ++i) {
        auto &item = info[i];
        if (item.width <= 0 || item.height <= 0)
          continue;
        Monitor m;
        if (char *name = item.name ? XGetAtomName(display, item.name) : nullptr) {
          m.name = std::string(name).substr(0, 64);
          XFree(name);
        }
        m.x = item.x;
        m.y = item.y;
        m.width = item.width;
        m.height = item.height;
        m.mm_width = item.mwidth;
        m.mm_height = item.mheight;
        m.primary = item.primary;
        m.refresh_mhz = 0;
        for (int j = 0; resources && j < item.noutput; ++j)
          if (XRROutputInfo *output =
                  XRRGetOutputInfo(display, resources, item.outputs[j])) {
            for (auto &mode : modes)
              if (output->crtc && mode.crtc == output->crtc)
                m.refresh_mhz = std::max(m.refresh_mhz, mode.refresh_mhz);
            XRRFreeOutputInfo(output);
          }
        if (m.refresh_mhz <= 0)
          m.refresh_mhz = fallback;
        list.push_back(m);
      }
      if (resources)
        XRRFreeScreenResources(resources);
      if (info)
        XRRFreeMonitors(info);
    }
    if (list.empty())
      for (auto &mode : modes) {
        if (mode.width <= 0 || mode.height <= 0)
          continue;
        Monitor m;
        m.name = mode.name;
        m.x = mode.x;
        m.y = mode.y;
        m.width = mode.width;
        m.height = mode.height;
        m.primary = mode.primary;
        m.refresh_mhz = mode.refresh_mhz > 0 ? mode.refresh_mhz : fallback;
        list.push_back(m);
      }
    if (list.empty()) {
      Monitor m;
      m.name = "X11";
      m.width = DisplayWidth(display, DefaultScreen(display));
      m.height = DisplayHeight(display, DefaultScreen(display));
      m.mm_width = DisplayWidthMM(display, DefaultScreen(display));
      m.mm_height = DisplayHeightMM(display, DefaultScreen(display));
      m.primary = true;
      m.refresh_mhz = fallback;
      list.push_back(m);
    }
    for (auto &m : list) {
      if (m.mm_width <= 0 || m.mm_height <= 0) {
        m.mm_width = estimated_mm(m.width);
        m.mm_height = estimated_mm(m.height);
      }
    }
    unique_monitor_names(list);
    return list;
  }
  static_assert(mode_interlace == RR_Interlace &&
                    mode_doublescan == RR_DoubleScan,
                "RandR mode flags");
  // Active CRTCs with their current mode timing (RandR 1.3); empty without
  // RandR, so callers fall back to default_refresh_mhz.
  std::vector<OutputMode> output_modes() const {
    std::vector<OutputMode> modes;
    if (headless || !randr_available)
      return modes;
    Window root = DefaultRootWindow(display);
    XRRScreenResources *resources =
        XRRGetScreenResourcesCurrent(display, root);
    if (!resources)
      return modes;
    RRCrtc primary = None;
    if (RROutput output = XRRGetOutputPrimary(display, root)) {
      if (XRROutputInfo *info = XRRGetOutputInfo(display, resources, output)) {
        primary = info->crtc;
        XRRFreeOutputInfo(info);
      }
    }
    for (int i = 0; i < resources->ncrtc; ++i) {
      XRRCrtcInfo *crtc =
          XRRGetCrtcInfo(display, resources, resources->crtcs[i]);
      if (!crtc)
        continue;
      for (int j = 0; crtc->mode != None && j < resources->nmode; ++j) {
        const XRRModeInfo &mode = resources->modes[j];
        if (mode.id != crtc->mode)
          continue;
        OutputMode m;
        m.x = crtc->x;
        m.y = crtc->y;
        m.width = int32_t(crtc->width);
        m.height = int32_t(crtc->height);
        m.refresh_mhz = mode_refresh_mhz(mode.dotClock, mode.hTotal,
                                         mode.vTotal, mode.modeFlags);
        m.primary = resources->crtcs[i] == primary;
        m.crtc = resources->crtcs[i];
        for (int k = 0; m.name.empty() && k < crtc->noutput; ++k)
          if (XRROutputInfo *info =
                  XRRGetOutputInfo(display, resources, crtc->outputs[k])) {
            if (info->name)
              m.name = std::string(info->name, info->nameLen).substr(0, 64);
            XRRFreeOutputInfo(info);
          }
        modes.push_back(m);
        break;
      }
      XRRFreeCrtcInfo(crtc);
    }
    XRRFreeScreenResources(resources);
    return modes;
  }
  int32_t output_refresh_mhz() const {
    return select_refresh_mhz(output_modes());
  }
  void current() {
    if (!headless)
      ensure(eglMakeCurrent(egl, scratch, scratch, context),
             "Cannot select X11 render context");
  }
  Atom atom(const char *s) { return XInternAtom(display, s, False); }
  void property(Window w, const char *name, const std::string &value) {
    if (headless)
      return;
    XChangeProperty(display, w, atom(name), atom("UTF8_STRING"), 8,
                    PropModeReplace, (const unsigned char *)value.data(),
                    value.size());
  }
  std::shared_ptr<Image> shm(int w, int h, int stride,
                             const unsigned char *data, bool alpha,
                             bool known = true) {
    ensure(w > 0 && h > 0 && w <= 8192 && h <= 8192 &&
               uint64_t(w) * h <= 16777216 && stride >= w * 4,
           "Guest SHM surface exceeds bounds");
    if (!known)
      return placeholder(w, h);
    current();
    auto i = image(w, h);
    if (headless)
      return i;
    // SHM contains guest-drawn CSD/menu pixels, never Android GPU readback.
    std::vector<unsigned char> rgba(size_t(w) * h * 4);
    for (int y = 0; y < h; ++y)
      for (int x = 0; x < w; ++x) {
        auto src = data + size_t(y) * stride + x * 4;
        auto dst = rgba.data() + (size_t(y) * w + x) * 4;
        dst[0] = src[2];
        dst[1] = src[1];
        dst[2] = src[0];
        dst[3] = alpha ? src[3] : 255;
      }
    glBindTexture(GL_TEXTURE_2D, i->texture);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE,
                    rgba.data());
    ensure(glGetError() == GL_NO_ERROR, "Cannot upload guest SHM surface");
    return i;
  }
  // Snapshot a validated layout (see dma_check) into an owned texture.
  // Import failure is not fatal: the surface shows a black placeholder.
  // With `cache`, the EGLImage is created once per guest buffer and reused
  // for every later frame Android renders into that allocation.
  // With `release_fence`, the caller gets a sync-file fd that signals when the
  // copy is done and must release the guest buffer only then (-1: already
  // complete, release now).
  std::shared_ptr<Image> dmabuf(const DmaLayout &layout, int w, int h,
                                std::shared_ptr<DmaImport> *cache = nullptr,
                                int *release_fence = nullptr) {
    if (release_fence)
      *release_fence = -1;
    current();
    if (headless)
      return image(w, h);
    std::shared_ptr<DmaImport> imported = cache ? *cache : nullptr;
    if (!imported) {
      imported = import_dmabuf(layout, w, h);
      if (!imported)
        return placeholder(w, h);
      if (cache)
        *cache = imported;
    }
    auto i = image(w, h);
    if (!copy_fbo)
      glGenFramebuffers(1, &copy_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, copy_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                           i->texture, 0);
    ensure(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
           "Cannot cache Android surface");
    glViewport(0, 0, w, h);
    glDisable(GL_BLEND);
    const float vertices[] = {-1, -1, 1, -1, -1, 1, 1, 1},
                coords[] = {0, 0, 1, 0, 0, 1, 1, 1};
    quad(imported->texture, true, vertices, coords);
    // The guest may reuse its buffer as soon as it is released; the copy
    // must have completed first: either a fence the caller waits on, or a
    // blocking finish when native fences are unavailable.
    if (!release_fence || !(*release_fence = copy_fence(), *release_fence >= 0))
      glFinish();
    auto error = glGetError();
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    ensure(error == GL_NO_ERROR,
           "Cannot snapshot Android DMA-BUF before release");
    return i;
  }
  int copy_fence() {
    if (!create_sync)
      return -1;
    const EGLint attributes[] = {EGL_SYNC_NATIVE_FENCE_FD_ANDROID,
                                 EGL_NO_NATIVE_FENCE_FD_ANDROID, EGL_NONE};
    auto sync = create_sync(egl, EGL_SYNC_NATIVE_FENCE_ANDROID, attributes);
    if (sync == EGL_NO_SYNC_KHR)
      return -1;
    glFlush(); // the fence fd exists only once the command stream is flushed
    int fd = dup_fence(egl, sync);
    destroy_sync(egl, sync);
    return fd >= 0 ? fd : -1;
  }
  bool releases_with_fences() const { return create_sync != nullptr; }
  std::shared_ptr<DmaImport> import_dmabuf(const DmaLayout &layout, int w, int h) {
    static const EGLint plane_attrs[3][3] = {
        {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE0_OFFSET_EXT,
         EGL_DMA_BUF_PLANE0_PITCH_EXT},
        {EGL_DMA_BUF_PLANE1_FD_EXT, EGL_DMA_BUF_PLANE1_OFFSET_EXT,
         EGL_DMA_BUF_PLANE1_PITCH_EXT},
        {EGL_DMA_BUF_PLANE2_FD_EXT, EGL_DMA_BUF_PLANE2_OFFSET_EXT,
         EGL_DMA_BUF_PLANE2_PITCH_EXT}};
    ensure(layout.planes >= 1 && layout.planes <= 3,
           "Unsupported DMA-BUF plane count");
    auto import = [&](bool hints) {
      std::vector<EGLint> attrs = {EGL_WIDTH, w, EGL_HEIGHT, h,
                                   EGL_LINUX_DRM_FOURCC_EXT,
                                   EGLint(layout.import)};
      for (int p = 0; p < layout.planes; ++p) {
        attrs.insert(attrs.end(),
                     {plane_attrs[p][0], layout.fd[p], plane_attrs[p][1],
                      EGLint(layout.offset[p]), plane_attrs[p][2],
                      EGLint(layout.stride[p])});
      }
      if (hints)
        attrs.insert(attrs.end(),
                     {EGL_YUV_COLOR_SPACE_HINT_EXT,
                      dma_bt709(w, h) ? EGL_ITU_REC709_EXT : EGL_ITU_REC601_EXT,
                      EGL_SAMPLE_RANGE_HINT_EXT, EGL_YUV_NARROW_RANGE_EXT});
      attrs.push_back(EGL_NONE);
      return create_image(egl, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr,
                          attrs.data());
    };
    auto image = import(layout.yuv);
    if (image == EGL_NO_IMAGE_KHR && layout.yuv)
      image = import(false);
    if (image == EGL_NO_IMAGE_KHR) {
      auto error = eglGetError();
      char line[160];
      snprintf(line, sizeof(line),
               "x11_dmabuf_import_failed format=%s planes=%d egl=0x%x",
               fourcc_name(layout.import).c_str(), layout.planes, error);
      report_once("import:" + fourcc_name(layout.import), line);
      return nullptr;
    }
    auto imported = std::make_shared<DmaImport>();
    imported->gl = gl;
    imported->image = image;
    imported->destroy = destroy_image;
    glGenTextures(1, &imported->texture);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, imported->texture);
    parameters(GL_TEXTURE_EXTERNAL_OES);
    bind_image(GL_TEXTURE_EXTERNAL_OES, image);
    return imported;
  }
  // Logs a diagnostic once per key (bounded), never per frame.
  void report_once(const std::string &key, const std::string &line) {
    if (reported.size() < 64 && reported.insert(key).second)
      fprintf(stderr, "%s\n", line.c_str());
  }
  NativeWindow create() {
    NativeWindow w;
    if (headless) {
      w.window = fake_id++;
      w.pw = physical_size(w.width);
      w.ph = physical_size(w.height);
      return w;
    }
    EGLint visual_id;
    eglGetConfigAttrib(egl, config, EGL_NATIVE_VISUAL_ID, &visual_id);
    XVisualInfo wanted{};
    wanted.visualid = visual_id;
    int count;
    auto *visual = XGetVisualInfo(display, VisualIDMask, &wanted, &count);
    ensure(visual && count, "No X11 visual for EGL");
    XSetWindowAttributes attr{};
    attr.background_pixel = attr.border_pixel = 0;
    attr.colormap = XCreateColormap(display, DefaultRootWindow(display),
                                    visual->visual, AllocNone);
    w.colormap = attr.colormap;
    w.pw = physical_size(w.width);
    w.ph = physical_size(w.height);
    attr.event_mask = ExposureMask | StructureNotifyMask | PointerMotionMask |
                      ButtonPressMask | ButtonReleaseMask | KeyPressMask |
                      KeyReleaseMask | FocusChangeMask | EnterWindowMask |
                      LeaveWindowMask | PropertyChangeMask;
    w.window = XCreateWindow(
        display, DefaultRootWindow(display), 0, 0, w.pw, w.ph, 0,
        visual->depth, InputOutput, visual->visual,
        CWColormap | CWEventMask | CWBackPixel | CWBorderPixel, &attr);
    XFree(visual);
    if (xi_opcode) {
      unsigned char bits[XIMaskLen(XI_LASTEVENT)]{};
      for (int event : {XI_Motion, XI_ButtonPress, XI_ButtonRelease})
        XISetMask(bits, event);
      if (touch_available)
        for (int event : {XI_TouchBegin, XI_TouchUpdate, XI_TouchEnd})
          XISetMask(bits, event);
      XIEventMask mask{XIAllMasterDevices, int(sizeof(bits)), bits};
      XISelectEvents(display, w.window, &mask, 1);
    }
    XSetWMProtocols(display, w.window, &wm_delete, 1);
    unsigned long hints[] = {2, 0, 0, 0, 0};
    XChangeProperty(display, w.window, atom("_MOTIF_WM_HINTS"),
                    atom("_MOTIF_WM_HINTS"), 32, PropModeReplace,
                    (unsigned char *)hints, 5);
    auto normal = atom("_NET_WM_WINDOW_TYPE_NORMAL");
    XChangeProperty(display, w.window, atom("_NET_WM_WINDOW_TYPE"), XA_ATOM, 32,
                    PropModeReplace, (unsigned char *)&normal, 1);
    w.surface = eglCreateWindowSurface(egl, config,
                                       (EGLNativeWindowType)w.window, nullptr);
    ensure(w.surface != EGL_NO_SURFACE,
           "Cannot create native X11 window surface");
    return w;
  }
  void destroy(NativeWindow &w) {
    if (!headless && w.window) {
      current();
      eglDestroySurface(egl, w.surface);
      XDestroyWindow(display, w.window);
      XFreeColormap(display, w.colormap);
    }
    w.window = 0;
  }
  void map(NativeWindow &w, bool show) {
    if (w.mapped == show)
      return;
    w.mapped = show;
    if (!headless) {
      if (show)
        XMapWindow(display, w.window);
      else
        XUnmapWindow(display, w.window);
      XFlush(display);
    }
    printf("x11_toplevel_%s=0x%lx\n", show ? "mapped" : "unmapped", w.window);
  }
  // Suspension stops the server saver/DPMS timers and is dropped by the X
  // server when this client disconnects. Desktop idle monitors (GNOME,
  // Cinnamon, KDE, XFCE) read the server idle time instead, so callers also
  // reset it periodically while inhibited, as xdg-screensaver does on X11.
  void idle_inhibit(bool on) {
    if (headless)
      return;
    if (saver_suspend)
      XScreenSaverSuspend(display, on);
    if (on)
      XResetScreenSaver(display);
    XFlush(display);
  }
  void idle_reset() {
    if (headless)
      return;
    XResetScreenSaver(display);
    XFlush(display);
  }
  void resize(NativeWindow &w, int width, int height) {
    if (w.width == width && w.height == height)
      return;
    if (headless) {
      w.width = width;
      w.height = height;
      w.pw = physical_size(width);
      w.ph = physical_size(height);
    } else {
      // This is only a request: the WM can defer/reject it during a grab.
      // ConfigureNotify owns the observed drawable size used for rendering.
      XResizeWindow(display, w.window, physical_size(width),
                    physical_size(height));
    }
  }
  // Zero leaves a bound unset, as xdg_toplevel min/max size does. Logical.
  void size_hints(NativeWindow &w, int min_w, int min_h, int max_w,
                  int max_h) {
    if (headless || !w.window)
      return;
    min_w = physical_size(min_w);
    min_h = physical_size(min_h);
    max_w = physical_size(max_w);
    max_h = physical_size(max_h);
    XSizeHints hints{};
    if (min_w > 0 || min_h > 0) {
      hints.flags |= PMinSize;
      hints.min_width = std::max(1, min_w);
      hints.min_height = std::max(1, min_h);
    }
    if (max_w > 0 || max_h > 0) {
      hints.flags |= PMaxSize;
      hints.max_width = max_w > 0 ? max_w : 32767;
      hints.max_height = max_h > 0 ? max_h : 32767;
    }
    if (w.position_hint) {  // keep a requested position across hint updates
      hints.flags |= USPosition;
      hints.x = w.hint_x;
      hints.y = w.hint_y;
    }
    XSetWMNormalHints(display, w.window, &hints);
  }
  // The part of the current desktop's _NET_WORKAREA on this monitor, in
  // physical root pixels; the whole monitor without a usable hint.
  Monitor workarea(const Monitor &monitor) {
    Monitor area = monitor;
    if (headless)
      return area;
    Atom type;
    int format;
    unsigned long count, left;
    unsigned char *bytes = nullptr;
    unsigned long desktop = 0;
    auto root_window = DefaultRootWindow(display);
    if (XGetWindowProperty(display, root_window, atom("_NET_CURRENT_DESKTOP"), 0,
                           1, False, XA_CARDINAL, &type, &format, &count, &left,
                           &bytes) == Success &&
        bytes) {
      if (type == XA_CARDINAL && format == 32 && count)
        desktop = *reinterpret_cast<unsigned long *>(bytes);
      XFree(bytes);
      bytes = nullptr;
    }
    if (desktop < 1024 &&
        XGetWindowProperty(display, root_window, atom("_NET_WORKAREA"),
                           desktop * 4, 4, False, XA_CARDINAL, &type, &format,
                           &count, &left, &bytes) == Success &&
        bytes) {
      if (type == XA_CARDINAL && format == 32 && count == 4) {
        auto *v = reinterpret_cast<unsigned long *>(bytes);
        // _NET_WORKAREA spans every monitor; keep its part on this one.
        long x0 = std::max<long>(long(v[0]), monitor.x),
             y0 = std::max<long>(long(v[1]), monitor.y),
             x1 = std::min<long>(long(v[0] + v[2]), long(monitor.x) + monitor.width),
             y1 = std::min<long>(long(v[1] + v[3]), long(monitor.y) + monitor.height);
        if (v[2] && v[3] && v[2] <= 65535 && v[3] <= 65535 && x1 > x0 && y1 > y0) {
          area.x = int32_t(x0);
          area.y = int32_t(y0);
          area.width = int32_t(x1 - x0);
          area.height = int32_t(y1 - y0);
        }
      }
      XFree(bytes);
    }
    return area;
  }
  // Ask the WM to map the window at a user-chosen position. USPosition makes
  // Muffin/Mutter/KWin/Xfwm honor it instead of their placement policy.
  void place(NativeWindow &w, int x, int y) {
    w.position_hint = true;
    w.hint_x = x;
    w.hint_y = y;
    if (headless || !w.window)
      return;
    XSizeHints *hints = XAllocSizeHints();
    long supplied = 0;
    if (!XGetWMNormalHints(display, w.window, hints, &supplied))
      hints->flags = 0;
    hints->flags |= USPosition;
    hints->x = x;
    hints->y = y;
    XSetWMNormalHints(display, w.window, hints);
    XFree(hints);
    XMoveWindow(display, w.window, x, y);
  }
  void frame_extents(NativeWindow &w, const WindowFrame &frame) {
    if (headless)
      return;
    // Logical margins; the drawable is logical x scale.
    unsigned long extents[] = {
        (unsigned long)std::lround(physical(frame.left)),
        (unsigned long)std::lround(physical(frame.right)),
        (unsigned long)std::lround(physical(frame.top)),
        (unsigned long)std::lround(physical(frame.bottom))};
    XChangeProperty(display, w.window, atom("_GTK_FRAME_EXTENTS"), XA_CARDINAL,
                    32, PropModeReplace,
                    reinterpret_cast<unsigned char *>(extents), 4);
  }
  void identity(NativeWindow &w, const std::string &app,
                const std::string &title) {
    if (headless)
      return;
    XClassHint hint{const_cast<char *>(app.c_str()),
                    const_cast<char *>(app.c_str())};
    XSetClassHint(display, w.window, &hint);
    property(w.window, "_NET_WM_NAME", title);
    property(w.window, "_KDE_NET_WM_DESKTOP_FILE", app);
  }
  void message(NativeWindow &w, const char *name, long a, long b = 0,
               long c = 0, long d = 0, long e = 0) {
    if (headless)
      return;
    XEvent event{};
    event.xclient.type = ClientMessage;
    event.xclient.window = w.window;
    event.xclient.message_type = atom(name);
    event.xclient.format = 32;
    event.xclient.data.l[0] = a;
    event.xclient.data.l[1] = b;
    event.xclient.data.l[2] = c;
    event.xclient.data.l[3] = d;
    event.xclient.data.l[4] = e;
    XSendEvent(display, DefaultRootWindow(display), False,
               SubstructureRedirectMask | SubstructureNotifyMask, &event);
    XFlush(display);
  }
  void activate(NativeWindow &w) {
    if (!headless)
      XMapRaised(display, w.window);
    message(w, "_NET_ACTIVE_WINDOW", 1, CurrentTime);
  }
  void initial_state(NativeWindow &w, bool maximized, bool fullscreen) {
    if (headless)
      return;
    std::vector<Atom> states;
    if (maximized) {
      states.push_back(atom("_NET_WM_STATE_MAXIMIZED_VERT"));
      states.push_back(atom("_NET_WM_STATE_MAXIMIZED_HORZ"));
    }
    if (fullscreen)
      states.push_back(atom("_NET_WM_STATE_FULLSCREEN"));
    XChangeProperty(
        display, w.window, atom("_NET_WM_STATE"), XA_ATOM, 32, PropModeReplace,
        reinterpret_cast<unsigned char *>(states.data()), states.size());
  }
  // A nonzero target size resamples the cursor (nearest) to physical pixels;
  // the hotspot is then in target pixels.
  void cursor(NativeWindow &w, const uint32_t *pixels, int width, int height,
              int x, int y, int target_w = 0, int target_h = 0) {
    if (headless)
      return;
    ensure(width > 0 && height > 0 && width <= 256 && height <= 256,
           "Invalid guest cursor");
    std::vector<uint32_t> resampled;
    if (target_w > 0 && target_h > 0 &&
        (target_w != width || target_h != height)) {
      target_w = std::min(target_w, 256);
      target_h = std::min(target_h, 256);
      resampled.resize(size_t(target_w) * target_h);
      for (int ty = 0; ty < target_h; ++ty)
        for (int tx = 0; tx < target_w; ++tx)
          resampled[size_t(ty) * target_w + tx] =
              pixels[size_t(int64_t(ty) * height / target_h) * width +
                     size_t(int64_t(tx) * width / target_w)];
      pixels = resampled.data();
      width = target_w;
      height = target_h;
      x = std::clamp(x, 0, width - 1);
      y = std::clamp(y, 0, height - 1);
    }
    ensure(x >= 0 && y >= 0 && x < width && y < height,
           "Invalid guest cursor");
    auto *image = XcursorImageCreate(width, height);
    ensure(image, "Cannot allocate X11 cursor");
    image->xhot = x;
    image->yhot = y;
    memcpy(image->pixels, pixels, size_t(width) * height * 4);
    auto cursor = XcursorImageLoadCursor(display, image);
    XcursorImageDestroy(image);
    XDefineCursor(display, w.window, cursor);
    XFreeCursor(display, cursor);
  }
  void render(NativeWindow &w, Surface &root, Rect geometry) {
    if (headless) {
      root.done();
      return;
    }
    ensure(eglMakeCurrent(egl, w.surface, w.surface, context),
           "Cannot select native app window");
    if (!w.swap_interval_set) {
      // Android already paces frames. The desktop compositor owns scanout;
      // an additional EGL vblank wait serializes input behind presentation.
      ensure(eglSwapInterval(egl, 0), "Cannot set native X11 swap interval");
      w.swap_interval_set = true;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glViewport(0, 0, w.pw, w.ph);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    // Logical surface positions/sizes are drawn at logical x scale. A
    // viewported Android buffer (physical size, logical destination) is thus
    // sampled 1:1. Scaled edges snap to whole pixels so adjacent CSD and
    // content surfaces neither overlap nor leave seams.
    bool scaled = scale120 != scale_unit;
    auto edge = [&](double v) {
      return scaled ? std::round(physical(v)) : v;
    };
    root.walk(-geometry.x, -geometry.y, [&](Surface &s, double x, double y) {
      auto &state = s.current;
      auto [width, height] = s.size();
      double left = edge(x), top = edge(y), right = edge(x + width),
             bottom = edge(y + height);
      float x0 = 2 * left / w.pw - 1, y0 = 1 - 2 * top / w.ph,
            x1 = 2 * right / w.pw - 1, y1 = 1 - 2 * bottom / w.ph;
      const float vertices[] = {x0, y0, x1, y0, x0, y1, x1, y1};
      double tw = state.image->width, th = state.image->height;
      if (state.transform & 1)
        std::swap(tw, th);
      tw /= state.scale;
      th /= state.scale;
      Rect src = state.source.w < 0 ? Rect{0, 0, tw, th} : state.source;
      float coords[8];
      for (int n = 0; n < 4; ++n) {
        double u = (src.x + ((n & 1) ? src.w : 0)) / tw,
               v = (src.y + ((n & 2) ? src.h : 0)) / th;
        if (state.transform & 4)
          u = 1 - u;
        switch (state.transform & 3) {
        case 1: {
          auto t = u;
          u = 1 - v;
          v = t;
          break;
        }
        case 2:
          u = 1 - u;
          v = 1 - v;
          break;
        case 3: {
          auto t = u;
          u = v;
          v = 1 - t;
          break;
        }
        }
        coords[n * 2] = u;
        coords[n * 2 + 1] = v;
      }
      quad(state.image->texture, false, vertices, coords);
    });
    ensure(eglSwapBuffers(egl, w.surface), "Cannot present native app window");
    root.done();
  }
};
} // namespace x11host
