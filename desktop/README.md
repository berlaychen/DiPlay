# DiPlay Linux: native + backend/Web frontend

[繁體中文](README.zh-TW.md)

Two Linux entry points share the existing DiPlay/xcertplay Kotlin protocol core:

- `--mode native`: GTK 3 + GStreamer. No Electron, Chromium, Android or emulator.
- `--mode web`: Linux owns CarPlay/radios/authentication; WebCodecs displays compressed H.264 in a browser, AudioWorklet plays PCM, and touch/keys/microphone return to Linux.

Targets: **x86_64** (ASUS T100's 64-bit CPU) and **aarch64** (64-bit Raspberry Pi OS on Pi 4/5). Native T100 starts at 960x540 / H.264 / 30 fps. This is not a browser-only CarPlay implementation.

## Status: wireless-first engineering preview

The port uses actual upstream iAP2 authentication/control, AirPlay negotiation, encrypted streams, HID and microphone packetization, not a replacement mock protocol. A separate demo exercises synthetic video/audio.

**There has been no physical T100, Raspberry Pi, iPhone or automotive test during this port.** Compilation and test-pattern playback do not prove iPhone interoperability, wireless handover, hardware acceleration, Siri, echo cancellation, reconnect reliability or vehicle safety. Check the actual workflow and `VALIDATION.md` rather than assuming tests passed.

Not implemented in this preview: **Linux wired-USB CarPlay**, Windows, BYD HUD/vehicle APIs, automatic AP/pairing or a native settings editor. Shared wired-protocol types are retained only where the wireless core references them.

## Install dependencies and test the display

Use Debian 13 / Raspberry Pi OS Trixie 64-bit or Ubuntu 24.04, Python >=3.11 and Java >=17. Native mode needs a working X11/Wayland session; the Web backend needs no desktop.

```sh
./desktop/install-deps.sh
```

This installs apt packages only. It does not change firmware, boot settings, AP/pairing, firewall or startup services. Use a built preview from the fork's **Linux native and Web** Actions workflow, or build below.

```sh
# Synthetic test pattern and quiet tone, NOT CarPlay:
./desktop/diplay --demo --mode native

# Or Web (open http://127.0.0.1:8765 on this machine):
./desktop/diplay --demo --mode web
cat ~/.local/state/diplay/web-token
```

Paste the token into the browser and click Enable sound. Use a Chromium build with H.264 WebCodecs support first. Hardware decoding is browser/driver dependent. The backend does not decode/re-encode video for Web; audio is decoded to 48 kHz stereo PCM.

## The APK's built-in authentication is supported

Both Linux modes reuse `LocalMfiAuthenticationClient`, including P-256 signing of the existing digest (no double hashing). The public Git source excludes the runtime identity; upstream's experimental **release APK** includes it. A certificate alone is insufficient: its matching private key is required.

The importer reads only `assets/offline-mfi/identity.pk8` and `certificate.p7b`, never executes APK code, validates size/ZIP entries/P-256/key matching and a signature round trip, and writes owner-only local files. Existing different identities are never overwritten.

For the pinned upstream **0.2.8** APK:

```sh
./desktop/diplay-auth fetch-upstream --accept-experimental-identity
./desktop/diplay-auth check
```

This explicitly downloads the public release over HTTPS and verifies its pinned SHA-256. It is not an automatic background download. Alternatively import an APK already available locally:

```sh
./desktop/diplay-auth import-apk /path/to/DiPlay-0.2.8.apk \
  --sha256 9b36a0866608244e422053d6027706b4672d8f2f4a8be9eb7e612411ffb6bf48 \
  --accept-experimental-identity
```

After import, both modes use `~/.config/diplay/identity`. **No dongle or Remote MFi server is required for that local-authentication path.** Internet is needed for the optional initial download, not for local signing on each connection. Actual iOS acceptance still needs testing.

Upstream describes the identity as experimental and recovered from public firmware. It is not newly provisioned or Apple certified. Import checks key consistency/validity dates, **not Apple's trust, licensing or future acceptance**. Do not upload the key, APK, pairing state or tokens into Git/CI artifacts.

Remote MFi remains available by setting `[auth] mode="remote"`, an HTTPS `url`, and optionally an owner-only `token_file`. A self-generated certificate can test the code but cannot establish an Apple-trusted CarPlay identity.

### Optional private deployment bundle

The ordinary public preview is credential-free but includes the importer. To prepare an offline, private deployment package after import:

```sh
python3 desktop/package.py /tmp/diplay-private.tar.gz \
  --with-identity ~/.config/diplay/identity --private-deployment-bundle
```

Do not publish this archive. On the target, set `[auth].directory` to the absolute path of `desktop/runtime-identity` in the extracted bundle. The app never silently selects a bundled identity instead of the configured one.

## Configure actual wireless CarPlay

```sh
mkdir -p ~/.config/diplay
cp desktop/profiles/t100.toml ~/.config/diplay/config.toml
chmod 600 ~/.config/diplay/config.toml
# Use rpi4.toml or rpi5.toml for the corresponding Pi.
```

Pair iPhone with Linux using desktop Bluetooth settings or interactive `bluetoothctl`, approving on both sides. Put the paired iPhone's Bluetooth MAC in `network.phone`, and select the Linux adapter (`hci0` by default).

Prepare a dedicated **WPA2 Wi-Fi AP**. `interface`, `ssid`, `password`, `channel` and `address` must match the real AP. Joining a 5 GHz network does not prove AP mode works. Check `iw list` and `iw dev wlan0 info`; use your legal regulatory country/channel. Linux uses a normal AP, not Android's WifiP2pManager.

Optional explicit NetworkManager helper:

```sh
./desktop/setup-ap.sh ~/.config/diplay/config.toml --create-dedicated-ap
```

**This can disconnect regular Wi-Fi on that interface. Use the local console or Ethernet.** The helper creates its own profile, refuses to overwrite existing profiles, sets autoconnect off and prints cleanup instructions. Its AP address is `10.42.0.1`. The receiver does not otherwise change networking.

Use an isolated AP, not a public LAN: upstream media listeners allocate additional dynamic ports. Configure your firewall intentionally. The Web control endpoint is separate and authenticated.

```sh
./desktop/diplay --doctor --config ~/.config/diplay/config.toml
./desktop/diplay --mode native --config ~/.config/diplay/config.toml
# Or, not simultaneously on the same radio/ports:
./desktop/diplay --mode web --config ~/.config/diplay/config.toml
```

The application starts waiting, not connected. Bluetooth carries iAP2 bootstrap, the iPhone joins the advertised AP, and the protocol transitions to Wi-Fi. Use Reconnect after restoring connectivity. Unattended reconnection is an on-device validation item.

## Hardware and performance

| Target | Initial video | Important qualification |
| --- | --- | --- |
| T100 / Z3740 / 2 GB | 960x540 H.264 30 fps | Prefer native; confirm i965/VA-API, Broadcom AP/Bluetooth firmware, SST audio and touch |
| Pi 4 | 1280x720 H.264 30 fps | Confirm functioning V4L2 H.264 decoder, not just the factory name |
| Pi 5 | 1280x720 H.264 30 fps | Pi 5 has no dedicated H.264 decoder; profile explicitly uses software decode |

[Raspberry Pi processor documentation](https://www.raspberrypi.com/documentation/computers/processors.html) describes Pi 5 HEVC hardware versus other software codecs. The ARM64 CI host is not a Pi.

T100 requires 64-bit Linux userspace; its IA32 UEFI can use a compatible mixed-mode loader. This repository does not flash firmware or provide an OS installer. The JVM heap cap is 256 MB, **not total app memory**. Measure Python/GStreamer/JVM/native RSS and actual CPU/temperature. No boot-time/FPS/RAM guarantee is implied.

Native video is `appsrc -> h264parse -> decoder -> videoconvert -> gtksink`, with copies; **not zero-copy**. Auto mode reports the selected decoder and falls back explicitly to `avdec_h264` after initialization failure. `--doctor` only reports available capabilities, not successful hardware use.

Audio supports LPCM/AAC-LC/Opus RTP. Native uses system-default devices, preferably PipeWire for simultaneous streams. Microphone defaults off; enable only after output works. Web also requires browser microphone opt-in. Navigation ducking is conservative and still needs real phone/call validation.

## Remote Web access and security

Loopback is the default. For a remote browser, tunnel port 8765 over SSH to localhost, or configure non-loopback binding with `tls_certificate`, `tls_key`, `allowed_origin` (exact `https://host:port`, no path). Use HTTPS/localhost for WebCodecs and microphone. One controller, token/Host/Origin checks, strict control allowlist; no open CORS or unauthenticated LAN control.

Queues are bounded at IPC, media, WebSocket and AudioWorklet boundaries. Overflow requests a new keyframe or disconnects a slow viewer, rather than accumulating stale navigation. The browser UI supports single-pointer touch, Home, Back and Siri. Neither frontend claims full multi-touch gesture validation.

Private state lives under `$XDG_STATE_HOME/diplay` (usually `~/.local/state/diplay`). The receiver pairing key and Web token are different from the accessory identity. Upstream packet/key logging is suppressed. No automatic telemetry upload.

## Build/test

The desktop build is independent of Android Gradle modules. Choose either a Kotlin >=1.9 CLI plus Java >=17 and `libbcprov-java`, or standalone Gradle:

```sh
python3 desktop/build.py test
# Or with Gradle 8.14.3 installed:
gradle -p desktop fatJar coreTest

PYTHONPATH=desktop/python python3 -m pytest -q desktop/tests
```

CI builds/tests on x86_64 and ARM64, checks native synthetic playback, runs a real Chromium smoke on x86_64, and produces source-inclusive packages. No runtime accessory credentials are published. See [VALIDATION.md](VALIDATION.md), [ARCHITECTURE.md](ARCHITECTURE.md) and [NOTICE.md](NOTICE.md).
