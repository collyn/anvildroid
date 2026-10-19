# App compatibility observations — 2026-10-17

The diagnostic APK observed actual Android APIs and delivered input in a disposable managed Android 13 runtime. Per-app settings were applied and reset through the production worker compatibility API, with revision and generation checks. In the initial sequence, only the app was restarted between observations; full Android restarts were tested separately below.

Runtime: `r-af023c4a7dfd413299a3a48dc2faf475`, generation `83deaec71c854ee8b902e0bbb90e7ecd`, private native X11 display `:77`. The test app was `org.anvildroid.touchprobe`. It used read-only network permissions and logged `ConnectivityManager`, `WifiManager`, and `onTouchEvent` source/action/tool type. Host mouse clicks were delivered through the runtime's private X11 socket and authority.

| App observation | Baseline / after reset and app restart | Both overrides enabled and app restarted |
| --- | --- | --- |
| `ConnectivityManager.getActiveNetworkInfo()` | `none` | `WIFI(1)`, connected/available, `CONNECTED` |
| `WifiManager` | Disabled, state `1`, `UNINITIALIZED` | Enabled, state `3`, `COMPLETED`, link speed `65` Mbps |
| Actual network count / active network object | `0` / absent | `0` / absent |
| Real mouse click, Android event source | `0x2002` (`SOURCE_MOUSE`) | `0x1002` (`SOURCE_TOUCHSCREEN`) |
| Real mouse click, pointer tool | `MOUSE(3)` | `MOUSE(3)` |
| Input gesture completion | `ACTION_DOWN`, `ACTION_UP`, zero active pointers afterward | Same |

Fake Wi-Fi changes these legacy app-visible network APIs; the runtime remained isolated from external networks, and this result does not demonstrate internet access or a real Wi-Fi transport. Mouse-as-touch converted the input source but retained the mouse tool type. Apps that inspect tool type may distinguish it from a finger. The game-control injector's separate finger-event tests are not evidence that ordinary mouse compatibility changes tool type.

After reset and a second app restart, network and input-source observations returned to baseline. Both raw compatibility properties were empty again; the baseline had no explicit or inherited overrides. No unrelated package entries were changed.

The same-package isolation check also passed with two simultaneously running guests. The original runtime at generation `c1df11f801034ec398d547c9cb1d4bc6` retained empty overrides, no active network, disabled Wi-Fi and mouse source `0x2002`. The separate GAPPS runtime `r-dfeb5f745ea248bb973aebcb7146f72b`, generation `eff9a39753884c6fbfa948c12c2569c4`, showed fake connected Wi-Fi and mouse source `0x1002` for the same package while its overrides were enabled. Both observations came from app APIs and real clicks on their separate private displays. Only the second guest's settings changed.

A full Android stop/start changed the second guest's generation to `428717c5ffec43a394c0dea68feda65a`. The retained app still reported connected/enabled fake Wi-Fi and actual mouse source `0x1002`, with tool `MOUSE(3)`. This establishes persistence across Android restart. The second guest's overrides were then reset through the production API.

After a subsequent full Android restart at generation `1aa8b189547c431e825e721ad6825fc4`, the reset remained effective: the app reported no active network, disabled Wi-Fi, and an actual mouse down/up pair with source `0x2002`, tool `MOUSE(3)`, and zero active pointers afterward.

Generated evidence is retained locally as `target/acceptance/2026-10-17-compatibility-*.json`, `2026-10-17-isolation-*.json`, and `2026-10-17-compat-*.json`, including API responses, app action results, and timestamped app logcat. These checks demonstrate app-restart, reset, Android-restart persistence, and isolation between the two selected disposable runtimes.
