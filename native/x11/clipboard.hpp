/* SPDX-License-Identifier: MIT — asynchronous wl_data_device/X11 clipboard.
 * Transfers are bounded (32 MiB, 10 seconds); no pipe or INCR blocks rendering.
 */
#pragma once
#include <X11/extensions/Xfixes.h>
#include <functional>
#include <map>
#include <set>
#include <vector>
class Clipboard {
  struct Source {
    Clipboard *host;
    wl_resource *resource;
    std::vector<std::string> mimes;
    bool used = false;
  };
  struct Offer {
    Clipboard *host;
    std::shared_ptr<Source> source;
    uint64_t epoch;
    std::map<std::string, Atom> targets;
  };
  struct Transfer {
    Clipboard *host;
    int fd = -1;
    wl_event_source *watch = nullptr;
    Window window = 0;
    XSelectionRequestEvent request{};
    Atom target = 0;
    bool incoming = false, incr = false, writing = false, replied = false;
    uint32_t started = milliseconds();
    uint64_t epoch;
    size_t offset = 0;
    std::vector<unsigned char> bytes;
    ~Transfer() {
      if (watch)
        wl_event_source_remove(watch);
      if (fd >= 0)
        close(fd);
      if (window)
        XDestroyWindow(host->x, window);
    }
  };
  static constexpr size_t limit = 32 * 1024 * 1024, chunk = 65536;
  // wl_data_device.used_source has wire value 1, but older Wayland headers
  // (including Ubuntu 24.04's) do not define its enum name.
  static constexpr uint32_t used_source_error = 1;
  Display *x;
  wl_display *display;
  wl_event_loop *loop;
  wl_event_source *timer = nullptr;
  Window owner = 0, query = 0, external = 0;
  Atom selection = 0, property = 0, targets_atom = 0, incr_atom = 0;
  int fixes_event = 0;
  uint64_t epoch = 0;
  Time timestamp = CurrentTime;
  std::shared_ptr<Source> selected;
  std::set<wl_resource *> devices;
  std::map<std::string, Atom> targets;
  std::vector<std::unique_ptr<Transfer>> transfers;
  std::function<wl_client *()> focused;
  std::function<bool(wl_client *, uint32_t)> accept_serial;
  Atom atom(const char *name) { return XInternAtom(x, name, False); }
  Window window() {
    auto w =
        XCreateSimpleWindow(x, DefaultRootWindow(x), -1, -1, 1, 1, 0, 0, 0);
    XSelectInput(x, w, PropertyChangeMask);
    return w;
  }
  void reply(Transfer &t, bool success) {
    XEvent event{};
    event.xselection = {SelectionNotify,
                        0,
                        True,
                        x,
                        t.request.requestor,
                        t.request.selection,
                        t.request.target,
                        success ? t.request.property : None,
                        t.request.time};
    XSendEvent(x, t.request.requestor, False, 0, &event);
    t.replied = true;
  }
  void cancel() {
    for (auto &t : transfers)
      if (!t->incoming && !t->replied)
        reply(*t, false);
    transfers.clear();
  }
  void changed() {
    ++epoch;
    cancel();
    if (selected && selected->resource)
      wl_data_source_send_cancelled(selected->resource);
    selected.reset();
    targets.clear();
  }
  void offer(wl_resource *device) {
    if (wl_resource_get_client(device) != focused()) {
      wl_data_device_send_selection(device, nullptr);
      return;
    }
    if (!selected && targets.empty()) {
      wl_data_device_send_selection(device, nullptr);
      return;
    }
    auto *state = new Offer{this, selected, epoch, targets};
    static const struct wl_data_offer_interface ops = {
        [](wl_client *, wl_resource *, uint32_t, const char *) {},
        [](wl_client *, wl_resource *r, const char *mime, int32_t fd) {
          auto *o = get<Offer>(r);
          if (o->epoch != o->host->epoch ||
              wl_resource_get_client(r) != o->host->focused()) {
            close(fd);
            return;
          }
          if (o->source) {
            if (o->source->resource &&
                std::find(o->source->mimes.begin(), o->source->mimes.end(),
                          mime) != o->source->mimes.end())
              wl_data_source_send_send(o->source->resource, mime, fd);
            close(fd);
          } else {
            auto at = o->targets.find(mime);
            if (at == o->targets.end()) {
              close(fd);
              return;
            }
            auto &h = *o->host;
            if (h.transfers.size() >= 8) {
              close(fd);
              return;
            }
            auto t = std::make_unique<Transfer>();
            t->host = &h;
            t->incoming = true;
            t->fd = fd;
            t->epoch = h.epoch;
            t->window = h.window();
            t->target = at->second;
            fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
            XConvertSelection(h.x, h.selection, t->target, h.property,
                              t->window, CurrentTime);
            h.transfers.push_back(std::move(t));
          }
        },
        destroy_request,
        [](wl_client *, wl_resource *r) {
          wl_resource_post_error(r, WL_DATA_OFFER_ERROR_INVALID_FINISH,
                                 "selection cannot finish as a drag");
        },
        [](wl_client *, wl_resource *, uint32_t, uint32_t) {}};
    auto *r = resource(wl_resource_get_client(device), &wl_data_offer_interface,
                       wl_resource_get_version(device), 0, &ops, state,
                       [](wl_resource *r) { delete get<Offer>(r); });
    wl_data_device_send_data_offer(device, r);
    if (selected) {
      for (auto &m : selected->mimes)
        wl_data_offer_send_offer(r, m.c_str());
    } else
      for (auto &m : targets)
        wl_data_offer_send_offer(r, m.first.c_str());
    wl_data_device_send_selection(device, r);
  }
  void publish() {
    for (auto *d : devices)
      offer(d);
  }
  static int pipe_event(int, uint32_t flags, void *data) {
    auto &t = *static_cast<Transfer *>(data);
    auto &h = *t.host;
    bool done = false, failed = false;
    if (t.writing) {
      while (t.offset < t.bytes.size()) {
        auto n =
            write(t.fd, t.bytes.data() + t.offset, t.bytes.size() - t.offset);
        if (n < 0 && (errno == EAGAIN || errno == EINTR))
          break;
        if (n <= 0) {
          failed = true;
          break;
        }
        t.offset += size_t(n);
      }
      done = t.offset == t.bytes.size();
    } else {
      unsigned char bytes[16384];
      for (;;) {
        auto n = read(t.fd, bytes, sizeof(bytes));
        if (n < 0 && (errno == EAGAIN || errno == EINTR))
          break;
        if (n < 0) {
          failed = true;
          break;
        }
        if (!n) {
          done = true;
          break;
        }
        if (t.bytes.size() + size_t(n) > limit) {
          failed = true;
          break;
        }
        t.bytes.insert(t.bytes.end(), bytes, bytes + n);
      }
    }
    failed |= bool(flags & WL_EVENT_ERROR);
    if (!done && !failed)
      return 0;
    if (!t.incoming && !failed) {
      if (t.bytes.size() <= chunk) {
        XChangeProperty(h.x, t.request.requestor, t.request.property, t.target,
                        8, PropModeReplace, t.bytes.data(),
                        int(t.bytes.size()));
        h.reply(t, true);
      } else {
        unsigned long size = t.bytes.size();
        XSelectInput(h.x, t.request.requestor, PropertyChangeMask);
        XChangeProperty(h.x, t.request.requestor, t.request.property,
                        h.incr_atom, 32, PropModeReplace,
                        reinterpret_cast<unsigned char *>(&size), 1);
        t.incr = true;
        wl_event_source_remove(t.watch);
        t.watch = nullptr;
        close(t.fd);
        t.fd = -1;
        h.reply(t, true);
        XFlush(h.x);
        return 0;
      }
    } else if (!t.incoming && !t.replied)
      h.reply(t, false);
    h.erase(&t);
    return 0;
  }
  void erase(Transfer *t) {
    transfers.erase(std::remove_if(transfers.begin(), transfers.end(),
                                   [t](auto &p) { return p.get() == t; }),
                    transfers.end());
  }
  void finish_incoming(Transfer &t) {
    t.writing = true;
    t.watch =
        wl_event_loop_add_fd(loop, t.fd, WL_EVENT_WRITABLE, pipe_event, &t);
    pipe_event(t.fd, WL_EVENT_WRITABLE, &t);
  }
  bool read_property(Window w, Atom &type, int &format,
                     std::vector<unsigned char> &bytes) {
    unsigned long count, left;
    unsigned char *data = nullptr;
    if (XGetWindowProperty(x, w, property, 0, limit / 4 + 1, True,
                           AnyPropertyType, &type, &format, &count, &left,
                           &data) != Success)
      return false;
    bool valid = !left && count * (format / 8) <= limit;
    if (valid && data && format == 8)
      bytes.assign(data, data + count);
    else if (valid && data && format == 32) {
      auto *values = reinterpret_cast<unsigned long *>(data);
      for (unsigned long i = 0; i < count; ++i) {
        auto v = uint32_t(values[i]);
        auto *p = reinterpret_cast<unsigned char *>(&v);
        bytes.insert(bytes.end(), p, p + 4);
      }
    }
    if (data)
      XFree(data);
    return valid;
  }

public:
  Clipboard(wl_display *d, Display *native, std::function<wl_client *()> focus,
            std::function<bool(wl_client *, uint32_t)> valid_serial)
      : x(native), display(d), loop(wl_display_get_event_loop(d)),
        focused(std::move(focus)), accept_serial(std::move(valid_serial)) {
    if (x) {
      owner = window();
      query = window();
      selection = atom("CLIPBOARD");
      property = atom("_ANVILDROID_CLIPBOARD");
      targets_atom = atom("TARGETS");
      incr_atom = atom("INCR");
      int error;
      if (XFixesQueryExtension(x, &fixes_event, &error))
        XFixesSelectSelectionInput(x, owner, selection,
                                   XFixesSetSelectionOwnerNotifyMask |
                                       XFixesSelectionWindowDestroyNotifyMask |
                                       XFixesSelectionClientCloseNotifyMask);
      external = XGetSelectionOwner(x, selection);
      if (external)
        XConvertSelection(x, selection, targets_atom, property, query,
                          CurrentTime);
    }
    timer = wl_event_loop_add_timer(
        loop,
        [](void *p) {
          auto &h = *static_cast<Clipboard *>(p);
          for (auto it = h.transfers.begin(); it != h.transfers.end();) {
            auto &t = **it;
            if (milliseconds() - t.started > 10000) {
              if (!t.incoming && !t.replied)
                h.reply(t, false);
              it = h.transfers.erase(it);
            } else
              ++it;
          }
          wl_event_source_timer_update(h.timer, 1000);
          return 0;
        },
        this);
    wl_event_source_timer_update(timer, 1000);
    wl_global_create(
        display, &wl_data_device_manager_interface, 3, this,
        [](wl_client *c, void *p, uint32_t v, uint32_t id) {
          static const struct wl_data_device_manager_interface ops = {
              [](wl_client *c, wl_resource *r, uint32_t id) {
                auto source = std::make_shared<Source>();
                source->host = get<Clipboard>(r);
                static const struct wl_data_source_interface ops = {
                    [](wl_client *, wl_resource *r, const char *mime) {
                      auto s = *get<std::shared_ptr<Source>>(r);
                      if (!s->used && s->mimes.size() < 256 &&
                          strlen(mime) < 256 && strchr(mime, '/'))
                        s->mimes.emplace_back(mime);
                    },
                    destroy_request,
                    [](wl_client *, wl_resource *, uint32_t) {}};
                source->resource = resource(
                    c, &wl_data_source_interface, wl_resource_get_version(r),
                    id, &ops, new std::shared_ptr<Source>(source),
                    [](wl_resource *r) {
                      auto *ref = get<std::shared_ptr<Source>>(r);
                      auto s = *ref;
                      s->resource = nullptr;
                      if (s->host->selected == s) {
                        s->host->changed();
                        if (s->host->x && XGetSelectionOwner(
                                              s->host->x, s->host->selection) ==
                                              s->host->owner)
                          XSetSelectionOwner(s->host->x, s->host->selection,
                                             None, CurrentTime);
                        s->host->publish();
                      }
                      delete ref;
                    });
              },
              [](wl_client *c, wl_resource *r, uint32_t id, wl_resource *) {
                auto *h = get<Clipboard>(r);
                static const struct wl_data_device_interface ops = {
                    [](wl_client *, wl_resource *r, wl_resource *,
                       wl_resource *, wl_resource *, uint32_t) {
                      wl_resource_post_error(r, WL_DISPLAY_ERROR_INVALID_METHOD,
                                             "drag and drop is not supported");
                    },
                    [](wl_client *c, wl_resource *r, wl_resource *source,
                       uint32_t serial) {
                      auto &h = *get<Clipboard>(r);
                      if (h.focused() != c || !h.accept_serial(c, serial))
                        return;
                      auto s = source ? *get<std::shared_ptr<Source>>(source)
                                      : std::shared_ptr<Source>{};
                      if (s && s->used) {
                        wl_resource_post_error(r, used_source_error,
                                               "source already used");
                        return;
                      }
                      h.changed();
                      h.selected = s;
                      if (s)
                        s->used = true;
                      if (h.x)
                        XSetSelectionOwner(h.x, h.selection, s ? h.owner : None,
                                           CurrentTime);
                      h.publish();
                    },
                    destroy_request};
                auto *device = resource(c, &wl_data_device_interface,
                                        wl_resource_get_version(r), id, &ops, h,
                                        [](wl_resource *r) {
                                          get<Clipboard>(r)->devices.erase(r);
                                        });
                h->devices.insert(device);
                h->offer(device);
              }};
          resource(c, &wl_data_device_manager_interface, std::min(v, 3u), id,
                   &ops, p);
        });
  }
  ~Clipboard() {
    cancel();
    wl_event_source_remove(timer);
    if (x) {
      XDestroyWindow(x, owner);
      XDestroyWindow(x, query);
    }
  }
  void focus() { publish(); }
  bool event(XEvent &e) {
    if (fixes_event && e.type == fixes_event + XFixesSelectionNotify) {
      auto &event = *reinterpret_cast<XFixesSelectionNotifyEvent *>(&e);
      if (event.selection != selection)
        return false;
      if (event.owner == owner) {
        timestamp = event.selection_timestamp;
        return true;
      }
      changed();
      external = event.owner;
      timestamp = event.selection_timestamp;
      // A fresh requestor prevents a previous owner's delayed reply being
      // accepted.
      XDestroyWindow(x, query);
      query = window();
      if (external)
        XConvertSelection(x, selection, targets_atom, property, query,
                          CurrentTime);
      publish();
      return true;
    }
    if (e.type == SelectionRequest && e.xselectionrequest.owner == owner) {
      auto request = e.xselectionrequest;
      if (!request.property)
        request.property = request.target;
      auto t = std::make_unique<Transfer>();
      t->host = this;
      t->request = request;
      t->epoch = epoch;
      t->target =
          request.target == atom("TEXT") ? atom("UTF8_STRING") : request.target;
      if (request.target == targets_atom) {
        std::vector<Atom> names{targets_atom, atom("TIMESTAMP")};
        if (selected)
          for (auto &m : selected->mimes)
            names.push_back(atom(m.c_str()));
        if (selected)
          for (auto &m : selected->mimes)
            if (m == "text/plain;charset=utf-8" || m == "text/plain") {
              names.push_back(atom("UTF8_STRING"));
              names.push_back(atom("TEXT"));
              break;
            }
        XChangeProperty(x, request.requestor, request.property, XA_ATOM, 32,
                        PropModeReplace,
                        reinterpret_cast<unsigned char *>(names.data()),
                        int(names.size()));
        reply(*t, true);
      } else if (request.target == atom("TIMESTAMP")) {
        unsigned long time = timestamp;
        XChangeProperty(x, request.requestor, request.property, XA_INTEGER, 32,
                        PropModeReplace,
                        reinterpret_cast<unsigned char *>(&time), 1);
        reply(*t, true);
      } else {
        char *name = XGetAtomName(x, request.target);
        std::string mime = name ? name : "";
        if (name)
          XFree(name);
        if ((mime == "UTF8_STRING" || mime == "TEXT") && selected) {
          for (auto &m : selected->mimes)
            if (m == "text/plain;charset=utf-8" || m == "text/plain") {
              mime = m;
              break;
            }
        }
        int fds[2];
        if (transfers.size() >= 8 || !selected || !selected->resource ||
            std::find(selected->mimes.begin(), selected->mimes.end(), mime) ==
                selected->mimes.end() ||
            pipe2(fds, O_CLOEXEC | O_NONBLOCK))
          reply(*t, false);
        else {
          t->fd = fds[0];
          // The source may use a blocking writer in its own process.
          fcntl(fds[1], F_SETFL, fcntl(fds[1], F_GETFL) & ~O_NONBLOCK);
          wl_data_source_send_send(selected->resource, mime.c_str(), fds[1]);
          close(fds[1]);
          t->watch = wl_event_loop_add_fd(loop, t->fd, WL_EVENT_READABLE,
                                          pipe_event, t.get());
          transfers.push_back(std::move(t));
        }
      }
      return true;
    }
    if (e.type == SelectionNotify && e.xselection.requestor == query) {
      if (!e.xselection.property ||
          XGetSelectionOwner(x, selection) != external)
        return true;
      Atom type;
      int format;
      std::vector<unsigned char> bytes;
      if (read_property(query, type, format, bytes) && type == XA_ATOM &&
          format == 32) {
        for (size_t i = 0; i + 4 <= bytes.size(); i += 4) {
          uint32_t id;
          memcpy(&id, bytes.data() + i, 4);
          char *name = XGetAtomName(x, id);
          if (name) {
            if (strchr(name, '/'))
              targets[name] = id;
            if (!strcmp(name, "UTF8_STRING"))
              targets["text/plain;charset=utf-8"] = targets["text/plain"] = id;
            XFree(name);
          }
        }
        publish();
      }
      return true;
    }
    for (auto &entry : transfers) {
      auto &t = *entry;
      if (t.incoming &&
          ((e.type == SelectionNotify && e.xselection.requestor == t.window) ||
           (t.incr && e.type == PropertyNotify &&
            e.xproperty.window == t.window && e.xproperty.atom == property &&
            e.xproperty.state == PropertyNewValue))) {
        if (e.type == SelectionNotify && !e.xselection.property) {
          erase(&t);
          return true;
        }
        Atom type;
        int format;
        std::vector<unsigned char> bytes;
        if (!read_property(t.window, type, format, bytes)) {
          erase(&t);
          return true;
        }
        if (type == incr_atom && format == 32 && !t.incr) {
          t.incr = true;
          return true;
        }
        if (format != 8 || t.bytes.size() + bytes.size() > limit) {
          erase(&t);
          return true;
        }
        t.bytes.insert(t.bytes.end(), bytes.begin(), bytes.end());
        if (!t.incr || bytes.empty())
          finish_incoming(t);
        return true;
      }
      if (!t.incoming && t.incr && e.type == PropertyNotify &&
          e.xproperty.window == t.request.requestor &&
          e.xproperty.atom == t.request.property &&
          e.xproperty.state == PropertyDelete) {
        size_t count = std::min(chunk, t.bytes.size() - t.offset);
        XChangeProperty(x, t.request.requestor, t.request.property, t.target, 8,
                        PropModeReplace,
                        count ? t.bytes.data() + t.offset : nullptr,
                        int(count));
        t.offset += count;
        if (!count)
          erase(&t);
        return true;
      }
    }
    return e.type == SelectionClear && e.xselectionclear.window == owner;
  }
};
