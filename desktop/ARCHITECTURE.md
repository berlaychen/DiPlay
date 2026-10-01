# Architecture

```text
iPhone -- Bluetooth / Wi-Fi AP -- Linux BlueZ + Avahi
                                     |
                         binary-plist framed IPC
                                     |
             selected DiPlay/xcertplay Kotlin/JVM protocol core
                  iAP2 + authentication + AirPlay + media/input
                                     |
                     decrypted encoded H.264 / audio RTP
                            /                    \
                  GTK + GStreamer            aiohttp WebSocket
                  native A/V                  WebCodecs + AudioWorklet
                            \                    /
                            touch / keys / microphone
```

`build.py prepare` selects original shared source files. Android logging is replaced with redacted desktop logging; unexpected Android dependencies fail the build. Two generated-source compatibility edits support Kotlin 1.9. Android source is not modified or copied into a second maintained protocol implementation.

`Receiver.kt` replaces Android lifecycle/controller ownership. BlueZ passes an RFCOMM FD through `BlockingDuplexByteStream`; upstream iAP2 performs authentication/control. Avahi advertises on the selected interface/address family. A Wi-Fi iAP2 tunnel is established before Bluetooth bootstrap is released. AP setup/pairing remain explicit operator actions.

`PipeMediaSink` forwards decoded framing/decrypted encoded payloads, not already decoded pixels. Linux GStreamer decodes native output; Web video remains compressed to the browser. Audio currently uses RTP depay/decoding before PCM Web output. Microphone packetization/encryption uses upstream code. Both UIs share local/remote authentication; identity import is deployment-only.

The Web split was cross-checked against LoopLink's `carplay-browser` branch. Its useful invariant is the same one used here: the backend owns CarPlay/authentication/platform networking while the browser owns presentation, touch and optional microphone capture. We deliberately do **not** inherit LoopLink's macOS/USB-only assumptions. The stable frontend/backend contract is documented in [WEB_PROTOCOL.md](WEB_PROTOCOL.md).

## IPC

u32 big-endian length, then binary plist dictionary; maximum 4 MiB. stdout contains only IPC. No Java deserialization, pickle, shell interpolation or base64 video.

Commands: start, bt_open/data/closed, touch, allowlisted key, keyframe, rendered, mic_packet, disconnect, stop. Events: ready/status/error/fatal, bt_send/release, video_config/video/stop, audio_start/audio/stop, mic_start/stop.

The Web endpoint cannot inject arbitrary core commands. Binary Web media uses a u32 JSON-header size, UTF-8 metadata and payload bytes. H.264 is Annex B; IDR frames include parameter sets. Audio is 48 kHz stereo signed LE 16-bit PCM. Browser microphone is stream ID plus 48 kHz mono LE PCM.

## Thread ownership

JVM owns protocol sessions. GLib owns BlueZ/Avahi, GTK and GStreamer lifecycle. Bounded queues bridge protocol I/O and UI. aiohttp owns its thread/loop. No blocking media decode in protocol receive callbacks. Slow viewers trigger resync/disconnect rather than unbounded latency. This is not a zero-copy design.

## Deferred

Linux USB/NCM; Windows adapters; BYD APIs; native full multitouch UI; automatic pairing/AP; zero-copy output; validated unattended vehicle deployment. Source presence is not implementation or validation of those features.
