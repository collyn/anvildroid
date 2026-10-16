/* SPDX-License-Identifier: MIT — native windows and GPU surface composition.
 * The guest bridge supplies every decoration/menu/overlay as ordinary surfaces.
 */
#pragma once
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
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <unistd.h>

namespace x11host {
inline void ensure(bool ok, const char *message) {
  if (!ok)
    throw std::runtime_error(message);
}
struct GpuImage : Image {
  ~GpuImage() override {
    if (texture)
      glDeleteTextures(1, &texture);
  }
};
struct NativeWindow {
  Window window = 0;
  Colormap colormap = 0;
  EGLSurface surface = EGL_NO_SURFACE;
  int width = 640, height = 480;
  bool mapped = false;
  bool swap_interval_set = false;
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
  GLint pos = 0, uv = 0;
  unsigned fake_id = 1;
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
    glUseProgram(p);
    pos = glGetAttribLocation(p, "position");
    uv = glGetAttribLocation(p, "texcoord");
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(external ? GL_TEXTURE_EXTERNAL_OES : GL_TEXTURE_2D, texture);
    glUniform1i(glGetUniformLocation(p, "image"), 0);
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
  explicit Platform(bool test = false) : headless(test) {
    if (test)
      return;
    XInitThreads();
    display = XOpenDisplay(nullptr);
    ensure(display, "Cannot connect to the selected X11 desktop");
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
    protocols = atom("WM_PROTOCOLS");
    wm_delete = atom("WM_DELETE_WINDOW");
    XSetLocaleModifiers("");
    xim = XOpenIM(display, nullptr, nullptr, nullptr);
    printf("x11_adapter_gpu=%s\n", glGetString(GL_RENDERER));
  }
  ~Platform() {
    if (headless)
      return;
    if (xim)
      XCloseIM(xim);
    current();
    glDeleteProgram(program);
    glDeleteProgram(external_program);
    eglMakeCurrent(egl, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(egl, scratch);
    eglDestroyContext(egl, context);
    eglTerminate(egl);
    XCloseDisplay(display);
  }
  bool testing() const { return headless; }
  std::pair<int, int> output_size() const {
    return headless
               ? std::make_pair(1920, 1080)
               : std::make_pair(DisplayWidth(display, DefaultScreen(display)),
                                DisplayHeight(display, DefaultScreen(display)));
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
                             const unsigned char *data, bool alpha) {
    ensure(w > 0 && h > 0 && w <= 8192 && h <= 8192 &&
               uint64_t(w) * h <= 16777216 && stride >= w * 4,
           "Guest SHM surface exceeds bounds");
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
  std::shared_ptr<Image> dmabuf(int fd, int w, int h, int stride, int offset,
                                uint32_t format, uint64_t modifier) {
    ensure(modifier == 0, "Only negotiated LINEAR DMA-BUFs are accepted");
    current();
    auto i = image(w, h);
    if (headless)
      return i;
    EGLint attrs[] = {EGL_WIDTH,
                      w,
                      EGL_HEIGHT,
                      h,
                      EGL_LINUX_DRM_FOURCC_EXT,
                      (EGLint)format,
                      EGL_DMA_BUF_PLANE0_FD_EXT,
                      fd,
                      EGL_DMA_BUF_PLANE0_OFFSET_EXT,
                      offset,
                      EGL_DMA_BUF_PLANE0_PITCH_EXT,
                      stride,
                      EGL_NONE};
    auto imported = create_image(egl, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT,
                                 nullptr, attrs);
    ensure(imported != EGL_NO_IMAGE_KHR,
           "Cannot import negotiated LINEAR DMA-BUF");
    GLuint texture, fbo;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_EXTERNAL_OES, texture);
    parameters(GL_TEXTURE_EXTERNAL_OES);
    bind_image(GL_TEXTURE_EXTERNAL_OES, imported);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                           i->texture, 0);
    ensure(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
           "Cannot cache Android surface");
    glViewport(0, 0, w, h);
    glDisable(GL_BLEND);
    const float vertices[] = {-1, -1, 1, -1, -1, 1, 1, 1},
                coords[] = {0, 0, 1, 0, 0, 1, 1, 1};
    quad(texture, true, vertices, coords);
    glFinish();
    auto error = glGetError();
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDeleteFramebuffers(1, &fbo);
    glDeleteTextures(1, &texture);
    destroy_image(egl, imported);
    ensure(error == GL_NO_ERROR,
           "Cannot snapshot Android DMA-BUF before release");
    return i;
  }
  NativeWindow create() {
    NativeWindow w;
    if (headless) {
      w.window = fake_id++;
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
    attr.event_mask = ExposureMask | StructureNotifyMask | PointerMotionMask |
                      ButtonPressMask | ButtonReleaseMask | KeyPressMask |
                      KeyReleaseMask | FocusChangeMask | EnterWindowMask |
                      LeaveWindowMask | PropertyChangeMask;
    w.window = XCreateWindow(
        display, DefaultRootWindow(display), 0, 0, w.width, w.height, 0,
        visual->depth, InputOutput, visual->visual,
        CWColormap | CWEventMask | CWBackPixel | CWBorderPixel, &attr);
    XFree(visual);
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
  void resize(NativeWindow &w, int width, int height) {
    if (w.width == width && w.height == height)
      return;
    if (headless) {
      w.width = width;
      w.height = height;
    } else {
      // This is only a request: the WM can defer/reject it during a grab.
      // ConfigureNotify owns the observed drawable size used for rendering.
      XResizeWindow(display, w.window, width, height);
    }
  }
  void frame_extents(NativeWindow &w, const WindowFrame &frame) {
    if (headless)
      return;
    unsigned long extents[] = {
        (unsigned long)frame.left, (unsigned long)frame.right,
        (unsigned long)frame.top, (unsigned long)frame.bottom};
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
  void cursor(NativeWindow &w, const uint32_t *pixels, int width, int height,
              int x, int y) {
    if (headless)
      return;
    ensure(width > 0 && height > 0 && width <= 256 && height <= 256 && x >= 0 &&
               y >= 0 && x < width && y < height,
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
    glViewport(0, 0, w.width, w.height);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    root.walk(-geometry.x, -geometry.y, [&](Surface &s, double x, double y) {
      auto &state = s.current;
      auto [width, height] = s.size();
      float x0 = 2 * x / w.width - 1, y0 = 1 - 2 * y / w.height,
            x1 = 2 * (x + width) / w.width - 1,
            y1 = 1 - 2 * (y + height) / w.height;
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
