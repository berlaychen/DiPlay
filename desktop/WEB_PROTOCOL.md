# DiPlay Linux Web protocol

This document defines the boundary between the Linux CarPlay backend and the browser frontend.
It is intentionally platform-neutral so the same frontend can later be reused by Windows or a
different Linux service implementation.

The design follows the same successful separation demonstrated by LoopLink's
`carplay-browser` branch, while keeping DiPlay Linux's wireless-first BlueZ/Avahi/AP backend.
LoopLink's current desktop branch is macOS/USB-oriented; those platform assumptions are not
part of this contract.

## Ownership

The **backend owns**:

- iAP2, AirPlay and CarPlay session state
- accessory authentication and the local/remote MFi provider
- Bluetooth bootstrap and Wi-Fi handoff
- pairing state and receiver identity
- CarPlay stream decryption
- microphone RTP encryption/packetization
- keyframe recovery and disconnect/reconnect policy
- all private credentials

The **browser owns**:

- H.264 decode via WebCodecs
- canvas presentation
- local touch/pointer capture
- optional browser microphone permission/capture
- local audio playback
- fullscreen and presentation UI

The browser MUST NOT receive the accessory private key, certificate private material,
receiver private pairing key, Wi-Fi passphrase, Bluetooth pairing secret, or raw debug payloads.

## Transport

The current Linux implementation serves static assets and a WebSocket from one backend origin.

- HTTP health: `GET /health`
- WebSocket: `GET /ws`
- Authentication: first WebSocket text frame MUST be
  `{"op":"auth","token":"..."}`
- Only one controller is accepted at a time.
- The token is stored as an owner-only file under the DiPlay state directory.
- Non-loopback deployments require an exact configured Origin and TLS.

### Binary media envelope

Binary WebSocket messages are:

```text
u32be json_header_length
UTF-8 JSON header
binary payload
```

The JSON header is limited to 4096 bytes. The backend bounds both event count and queued bytes.
Slow clients are resynchronized or disconnected; stale navigation video is never allowed to
grow without limit.

Current payload events:

- `video`: Annex-B H.264 access unit; header contains `key` and `time_us`
- `pcm`: signed little-endian 48 kHz stereo PCM (current compatibility path)

Control/status events are JSON text frames.

## Video

The backend negotiates H.264 for the Web frontend. It forwards compressed AVC instead of
decoding/re-encoding frames. The browser:

1. receives `video_config`
2. probes WebCodecs hardware/software preferences
3. waits for an IDR
4. decodes into `VideoFrame`
5. draws to the canvas
6. sends `{"op":"rendered"}` after the first rendered frame

Decoder failure is terminal for a WebCodecs instance. The frontend creates a fresh decoder,
requests a keyframe and bounds retry attempts. Queue growth triggers a keyframe resync.

This path is deliberately different from native Linux mode, where GStreamer owns decode and
presentation.

## Audio

The current production-preview path decodes CarPlay RTP in the Linux backend with GStreamer and
sends bounded 48 kHz stereo PCM to the Web AudioWorklet. This is intentionally conservative:
LoopLink demonstrates browser-side AAC/Opus decode, but its published validation currently does
not claim audio/microphone validation.

A future protocol revision MAY add encoded audio events so WebCodecs AudioDecoder can own
AAC/Opus decode. That change must retain LPCM handling, RTP payload parsing, stream-role mixing,
backpressure and regression coverage before becoming the default.

## Microphone

Browser microphone access is opt-in. The current frontend captures mono PCM in an AudioWorklet
and sends bounded packets to Linux. Linux performs any codec conversion required by the active
CarPlay microphone stream; the JVM core performs the existing encrypted RTP packetization.

A future encoded-browser-microphone path may be added for Opus, but raw browser input must never
bypass format/size validation in the backend.

## Controls

The WebSocket accepts only an allowlisted schema. Current user actions are:

- single-pointer touch/release
- Home
- Back
- Siri
- keyframe request
- reconnect

Arbitrary JVM/core operations cannot be injected from the browser.

## Recovery

The backend remembers the latest status and AVC configuration. A newly authorized frontend gets
current state, then requests a fresh keyframe.

Media queues are bounded at every boundary:

```text
CarPlay socket
  -> JVM protocol core
  -> bounded binary-plist IPC
  -> Linux media broker
  -> bounded WebSocket queue
  -> WebCodecs / AudioWorklet
```

Overflow is treated as latency failure, not as a reason to buffer indefinitely.

## Platform adapters

This Web protocol does not depend on Android, BlueZ, GStreamer, libimobiledevice, WinUSB or a
specific radio implementation. Those belong behind the backend boundary.

That separation is deliberate:

- Linux x86_64: ASUS T100 and similar systems
- Linux arm64: Raspberry Pi 4/5
- future Windows backend: Media Foundation/WASAPI/WinRT or equivalent
- future wired Linux backend: a helper stream can provide the same `BlockingDuplexByteStream`
  seam used by the portable iAP2 core

## Validation boundary

Browser rendering tests validate the browser/backend contract, WebCodecs lifecycle, controls,
queue handling and synthetic media. They do **not** prove iPhone authentication, wireless
handoff, hardware acceleration, vehicle audio routing, Siri/calls or long-running automotive
reliability. Those require physical-device validation.
