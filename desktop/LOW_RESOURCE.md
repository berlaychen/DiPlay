# Low-resource integration: T100 and Pi 4/5

This implements selected PlayPort ideas without replacing the existing Linux
GTK/GStreamer frontend or independent Web frontend. Both continue to support
`--transport wired|wireless`. No credentials or Android sources are changed.

Reference reviewed: `youcci/playport@9a0882dd0ffe48e467b59d58b12d81391df55ade`.
The code here is newly implemented against the existing DiPlay contract; it does
not copy PlayPort's macOS-only bridge, media queue or browser protocol.

## What is integrated

| Feature | Native GTK | Web | Low-resource decision |
| --- | --- | --- | --- |
| Connection recovery | Shared backend | Shared backend | One 1 Hz policy timer, finite attempts, no radio reset |
| Idle broker | Shared backend | Shared backend | One-shot drain on events, replaces permanent 5 ms poll |
| Display presets | Settings dialog | Settings dialog | Only 800x480, 960x540, 1280x720; H.264/30 |
| Stream gains | GStreamer | Same backend before PCM | Existing codec pipeline; no extra encoder |
| Two-contact input | GDK sequences | Pointer events | Fixed slots; immediate edges, max 40 Hz coalesced motion |
| Preference persistence | Same file | Same file | Atomic Apply, not per-frame/per-slider disk writes |
| External AP deployment | Same network adapter | Same network adapter | No need for the receiver to be the AP |

Web has no framework/npm runtime dependency. Its frame counter updates at most
once a second after the first frame, not on every decoded frame. Video still
passes compressed H.264 to WebCodecs. GTK still requires no browser.

## Reconnect policy

Defaults under `[connection]` (existing profiles inherit these):

```toml
transport = "wired" # or "wireless"
auto_reconnect = true
retry_limit = 5
retry_initial = 3
retry_max = 60
startup_timeout = 90
stable_seconds = 30
```

The first connection is followed by at most five automatic retries, with default
backoffs of 3, 6, 12, 24 and 48 seconds. Setup/runtime timing adds to these delays.
Duplicate error messages do not move the deadline. A connected session must also
receive video and remain stable before its retry budget resets. A static CarPlay
map is **not** treated as a disconnection just because no new frames arrive.

An initial handshake with no usable video times out. Explicit disconnect pauses
recovery. Invalid configuration/permission failures are not repeatedly forced.
After the budget is exhausted, correct the cause and use Reconnect. Automatic
retry does not grant Trust, reset adapters, stop usbmuxd, create an AP, alter
firewalls, or acquire root. Existing explicitly configured USB preparation flags
retain their meaning. Local authentication remains entirely local after import.

The UI/backend remain alive across transient transport failures. Existing
session-generation checks discard old callbacks. Fatal protocol/IPC failures
still fail explicitly rather than being silently retried forever.

## Shared settings

Open **Settings** in either UI, choose a low-resource preset and click **Apply**.
Display changes restart the phone session, so apply them only while parked.
Volume-only changes do not restart it. Master, media, guidance, speech and
telephony gains multiply the existing conservative navigation ducking. They do
not change microphone capture gain, provide echo cancellation or certify calls.

Preferences are saved as `presentation.json` (0600) in the private state directory
(default `~/.local/state/diplay`, directory 0700). The file contains only a schema
version, preset and five gains. It never contains Wi-Fi credentials, accessory
keys or Web tokens. The original TOML is not overwritten. Persisted presentation
values override its video dimensions on the next launch; remove this one JSON
file while stopped to return to the TOML profile. An unchanged Apply does not
write to disk. Invalid/unwritable state is reported, not partially applied.

The authenticated WebSocket accepts `presentation` with an optional allowlisted
`preset` and a bounded `volumes` object. No arbitrary file, pipeline, transport or
network configuration can be injected. All settings use the existing token,
Host/Origin, one-controller, size and queue checks. Expensive repeated controls
are coalesced/rate-limited. Native and Web settings share the same implementation.

## Input and cancellation

Contacts always keep their two HID slot indexes. Releasing finger zero never
renumbers finger one. Press/release edges are sent immediately, so a short tap
cannot disappear within one rendering interval. Only motion is delayed/coalesced
for up to 25 ms. Blur, cancelled touches, disconnect and teardown release contacts
and cancel pending motion. Keyboard actions do not escape settings controls or
modified browser shortcuts. GDK touch events are handled directly, not through
an additional mouse-emulation layer.

## External access point

Use this when the receiver cannot create a reliable AP. The receiver and phone
must be on a mutually reachable LAN, with no client isolation. The receiver may
use Ethernet; the SSID/channel/password describe the AP the **iPhone** joins.

```toml
[network]
topology = "external"
interface = "eth0"           # or the receiver's existing Wi-Fi interface
address = "192.168.8.20"     # receiver address, NOT router/phone address
bluetooth_adapter = "hci0"
phone = "REPLACE_WITH_PAIRED_IPHONE_MAC"
ssid = "YOUR_EXTERNAL_AP"
password = "REPLACE_WITH_AP_PASSWORD"
channel = 36                # must match the actual AP; 2.4 GHz e.g. 6 is allowed
```

Do not run `setup-ap.sh` for this topology; the helper explicitly refuses it.
Pairing/mDNS/firewall/network reachability remain deployment responsibilities.
This removes an AP-creation requirement, not all driver/2.4 GHz reliability risks.
It also does not remove the wired alternative or imply phone hotspot compatibility.

## Deliberately not integrated

- No automatic 4K/60fps/HEVC upgrade or pixel-ratio-based resolution expansion.
- No forced browser AAC/Opus path: PCM remains the tested compatibility path;
  moving decode on the same T100 is not a proven CPU/RAM win.
- No multi-viewer control/microphone competition; one authenticated controller.
- No new Node server, Electron runtime, second UI framework or telemetry service.
- No fake end-to-end latency, idle-frame failure detector or claim that a
  hardware-acceleration preference proves actual hardware use.
- No Windows adapter, certified identity, USB driver replacement or firmware flash.

## Validation

`python3 desktop/build.py test` builds the same portable core. Run:

```sh
PYTHONPATH=desktop/python python3 -m pytest -q desktop/tests
node --test desktop/tests/test_web_client.cjs desktop/tests/test_input.cjs
```

CI additionally uses real GTK/GStreamer/Xvfb and Chrome synthetic media. Tests
exercise settings on both UI paths, queues, actual decoded resolution, gain,
input cancellation and retry state. They do **not** validate a physical iPhone,
T100 or Raspberry Pi. Measure RSS, CPU, decoder choice, touch, calls, temperature
and cable/radio reconnection on those devices before vehicle deployment.
