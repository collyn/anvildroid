/* SPDX-License-Identifier: MIT — remembered X11 app-window positions.
 *
 * Wayland clients cannot place their windows, so the bridge's window profiles
 * keep only size and maximize state. X11 lets the client request a position,
 * so the adapter also remembers where each app window was last left and asks
 * the window manager (USPosition) to reopen it there.
 *
 * Storage: $XDG_STATE_HOME (else ~/.local/state)/anvildroid/
 * x11-window-positions-<runtime>.tsv, one "x<TAB>y<TAB>app_id" line per app,
 * in physical root pixels. Written atomically by the desktop user who runs the
 * adapter; a missing or unwritable file only disables the feature.
 */
#pragma once
#include "output-mode.hpp"
#include <algorithm>
#include <cstdio>
#include <fcntl.h>
#include <map>
#include <optional>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace x11host {

class WindowPositions {
public:
  static constexpr size_t max_entries = 512;

  // Runtime IDs are "default" or "r-<hex>"; anything else falls back to
  // "default" so the name never leaves the state directory.
  static std::string default_path(const char *runtime, const char *state_home,
                                  const char *home) {
    std::string id = runtime && *runtime ? runtime : "default";
    for (char c : id)
      if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-')) {
        id = "default";
        break;
      }
    if (id.size() > 64)
      id = "default";
    std::string base;
    if (state_home && state_home[0] == '/')
      base = state_home;
    else if (home && home[0] == '/')
      base = std::string(home) + "/.local/state";
    else
      return {};
    return base + "/anvildroid/x11-window-positions-" + id + ".tsv";
  }

  explicit WindowPositions(std::string path = {}) : path_(std::move(path)) {}

  const std::string &path() const { return path_; }

  static bool valid_app(const std::string &app) {
    if (app.empty() || app.size() > 512)
      return false;
    for (unsigned char c : app)
      if (c < 0x20 || c == 0x7f)
        return false;
    return true;
  }

  void load() {
    entries_.clear();
    if (path_.empty())
      return;
    FILE *file = fopen(path_.c_str(), "re");
    if (!file)
      return;
    char line[600];
    while (fgets(line, sizeof line, file)) {
      std::string text(line);
      if (!text.empty() && text.back() == '\n')
        text.pop_back();
      auto first = text.find('\t');
      auto second = first == std::string::npos ? first : text.find('\t', first + 1);
      if (second == std::string::npos)
        continue;
      char *end = nullptr;
      long x = strtol(text.c_str(), &end, 10);
      if (end != text.c_str() + first)
        continue;
      long y = strtol(text.c_str() + first + 1, &end, 10);
      if (end != text.c_str() + second)
        continue;
      auto app = text.substr(second + 1);
      if (!valid_app(app) || x < -100000 || x > 100000 || y < -100000 || y > 100000)
        continue;
      remember(app, int(x), int(y));
    }
    fclose(file);
    dirty_ = false;
  }

  std::optional<std::pair<int, int>> lookup(const std::string &app) const {
    auto it = entries_.find(app);
    if (it == entries_.end())
      return std::nullopt;
    return std::make_pair(it->second.x, it->second.y);
  }

  void remember(const std::string &app, int x, int y) {
    if (!valid_app(app))
      return;
    auto &entry = entries_[app];
    if (entry.used && entry.x == x && entry.y == y)
      return;
    entry = {x, y, ++clock_};
    dirty_ = true;
    if (entries_.size() > max_entries) {
      auto oldest = entries_.begin();
      for (auto it = entries_.begin(); it != entries_.end(); ++it)
        if (it->second.used < oldest->second.used)
          oldest = it;
      entries_.erase(oldest);
    }
  }

  // Rectangle in physical root pixels (a monitor's part of the workarea).
  struct Area {
    int x = 0, y = 0, width = 0, height = 0;
  };
  // Same rule as the KWin script used on Wayland: a usable header stays on the
  // area — at least min(160, width) visible horizontally, and the top
  // min(64, height) rows fully inside it vertically.
  static bool fits(const Area &a, int x, int y, int width, int height) {
    int64_t w = std::max(width, 1), h = std::max(height, 1);
    int64_t visible = std::min<int64_t>(int64_t(x) + w, int64_t(a.x) + a.width) -
                      std::max<int64_t>(x, a.x);
    return visible >= std::min<int64_t>(160, w) && y >= a.y &&
           int64_t(y) + std::min<int64_t>(64, h) <= int64_t(a.y) + a.height;
  }
  static const Area *area_for(const std::vector<Area> &areas, int x, int y,
                              int width, int height) {
    for (auto &a : areas)
      if (fits(a, x, y, width, height))
        return &a;
    return nullptr;
  }
  static std::pair<int, int> centered(const Area &a, int width, int height) {
    return {a.x + std::max(0, (a.width - width) / 2),
            a.y + std::max(0, (a.height - height) / 2)};
  }
  // A window as large as the area (e.g. a task recreated maximized before the
  // WM sees its state) goes to the area origin on that axis.
  static std::pair<int, int> clamp_large(const Area &a, int x, int y, int width,
                                         int height) {
    return {width >= a.width ? a.x : x, height >= a.height ? a.y : y};
  }

  // Atomic replace; returns false (and keeps the data dirty) on any failure.
  bool save() {
    if (!dirty_ || path_.empty())
      return true;
    auto slash = path_.rfind('/');
    if (slash == std::string::npos || slash == 0)
      return false;
    auto directory = path_.substr(0, slash);
    // Create ~/.local/state/anvildroid (and ~/.local/state) privately if needed.
    auto parent = directory.substr(0, directory.rfind('/'));
    mkdir(parent.c_str(), 0700);
    mkdir(directory.c_str(), 0700);
    auto temporary = path_ + ".tmp";
    int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0)
      return false;
    std::string data;
    for (auto &[app, entry] : entries_)
      data += std::to_string(entry.x) + '\t' + std::to_string(entry.y) + '\t' + app + '\n';
    bool ok = write(fd, data.data(), data.size()) == ssize_t(data.size()) && fsync(fd) == 0;
    ok = close(fd) == 0 && ok;
    if (!ok || rename(temporary.c_str(), path_.c_str()) != 0) {
      unlink(temporary.c_str());
      return false;
    }
    dirty_ = false;
    return true;
  }

  bool dirty() const { return dirty_; }
  size_t size() const { return entries_.size(); }

private:
  struct Entry {
    int x = 0, y = 0;
    unsigned long used = 0;
  };
  std::string path_;
  std::map<std::string, Entry> entries_;
  unsigned long clock_ = 0;
  bool dirty_ = false;
};

} // namespace x11host
