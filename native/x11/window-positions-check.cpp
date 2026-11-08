// Remembered X11 window positions: path, persistence, bounds and visibility.
#include "window-positions.hpp"
#include <cstdlib>
#include <fstream>
#include <iostream>

using x11host::WindowPositions;

static int failures = 0;
#define CHECK(condition)                                                       \
  do {                                                                         \
    if (!(condition)) {                                                        \
      std::cerr << "FAIL " << __LINE__ << ": " #condition "\n";                \
      ++failures;                                                              \
    }                                                                          \
  } while (0)

int main() {
  // Paths stay inside the user's state directory whatever the runtime ID says.
  CHECK(WindowPositions::default_path("r-0123abcd", nullptr, "/home/u") ==
        "/home/u/.local/state/anvildroid/x11-window-positions-r-0123abcd.tsv");
  CHECK(WindowPositions::default_path(nullptr, "/state", "/home/u") ==
        "/state/anvildroid/x11-window-positions-default.tsv");
  CHECK(WindowPositions::default_path("../../etc", nullptr, "/home/u") ==
        "/home/u/.local/state/anvildroid/x11-window-positions-default.tsv");
  CHECK(WindowPositions::default_path("r-1", "relative", "/home/u") ==
        "/home/u/.local/state/anvildroid/x11-window-positions-r-1.tsv");
  CHECK(WindowPositions::default_path("r-1", nullptr, nullptr).empty());

  char directory[] = "/tmp/anvil-positions-XXXXXX";
  CHECK(mkdtemp(directory) != nullptr);
  std::string path = std::string(directory) + "/state/anvildroid/positions.tsv";

  WindowPositions saved(path);
  saved.remember("waydroid.anvildroid.r-1.com.example.game", 640, 220);
  saved.remember("waydroid.anvildroid.r-1.Waydroid", -1200, 40);
  saved.remember("bad\tname", 1, 1);  // control characters never reach the file
  saved.remember("", 1, 1);
  CHECK(saved.size() == 2);
  CHECK(saved.save());  // creates the missing state directories
  CHECK(!saved.dirty());

  WindowPositions loaded(path);
  loaded.load();
  CHECK(loaded.size() == 2);
  auto game = loaded.lookup("waydroid.anvildroid.r-1.com.example.game");
  CHECK(game && game->first == 640 && game->second == 220);
  auto full = loaded.lookup("waydroid.anvildroid.r-1.Waydroid");
  CHECK(full && full->first == -1200 && full->second == 40);
  CHECK(!loaded.lookup("unknown"));

  // Corrupt and out-of-range lines are ignored; good lines survive.
  {
    std::ofstream file(path);
    file << "10\t20\tgood.app\nnot-a-number\t5\tbad.app\n5\t5\n"
         << "999999\t0\tfar.app\n7\t8\twith\x01" "control\n";
  }
  loaded.load();
  CHECK(loaded.size() == 1);
  CHECK(loaded.lookup("good.app"));

  // Unchanged positions do not mark the store dirty; capacity is bounded.
  loaded.remember("good.app", 10, 20);
  CHECK(!loaded.dirty());
  for (size_t i = 0; i < WindowPositions::max_entries + 10; ++i)
    loaded.remember("app." + std::to_string(i), int(i), 0);
  CHECK(loaded.size() == WindowPositions::max_entries);
  CHECK(!loaded.lookup("good.app"));  // least recently used entry was dropped

  // Saved positions are reused only with a usable header on a workarea
  // (same rule as the Wayland KWin script); otherwise windows are centered.
  using Area = WindowPositions::Area;
  std::vector<Area> areas{{0, 32, 1920, 1048}, {1920, 0, 2560, 1440}};
  CHECK(WindowPositions::area_for(areas, 100, 100, 800, 600) == &areas[0]);
  CHECK(WindowPositions::area_for(areas, 3000, 200, 800, 600) == &areas[1]);
  CHECK(!WindowPositions::area_for(areas, 5000, 100, 800, 600));   // unplugged
  CHECK(!WindowPositions::area_for(areas, 100, 10, 800, 600));     // under the top panel
  CHECK(!WindowPositions::area_for(areas, 100, 1030, 800, 600));   // header off the bottom
  CHECK(!WindowPositions::area_for(areas, -790, 100, 800, 600));   // only a sliver visible
  CHECK(WindowPositions::area_for(areas, -640, 100, 800, 600));    // exactly 160px visible
  CHECK(!WindowPositions::area_for(areas, -641, 100, 800, 600));   // 159px is not enough
  CHECK(WindowPositions::fits(areas[0], 0, 100, 120, 600));        // narrow windows need their width
  CHECK(!WindowPositions::area_for({}, 100, 100, 800, 600));
  auto centered = WindowPositions::centered(areas[0], 800, 600);
  CHECK(centered.first == 560 && centered.second == 256);
  auto huge = WindowPositions::centered(areas[0], 2400, 1200);
  CHECK(huge.first == 0 && huge.second == 32);
  auto clamped = WindowPositions::clamp_large(areas[1], 2500, 300, 2560, 900);
  CHECK(clamped.first == 1920 && clamped.second == 300);

  // An unwritable location fails without throwing and stays dirty.
  WindowPositions blocked("/proc/anvil-positions/x.tsv");
  blocked.remember("a.b", 1, 2);
  CHECK(!blocked.save());
  CHECK(blocked.dirty());

  std::string cleanup = std::string("rm -rf ") + directory;
  CHECK(system(cleanup.c_str()) == 0);
  if (failures)
    return 1;
  std::cout << "PASS window positions\n";
  return 0;
}
