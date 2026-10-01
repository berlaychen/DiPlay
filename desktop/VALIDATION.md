# Validation scope

The workflow `Linux native and Web` runs on generic x86_64 and ARM64 Ubuntu hosts, **not T100/Pi hardware**. Inspect the current run; this document defines checks, not a blanket pass claim.

## Automated

- Compile selected Kotlin/JVM code without Android SDK/runtime.
- 11 core self-tests (including wired IPC isolation and wired-only CarPlay endpoint encoding): IPC roundtrip/size/truncation, RFCOMM fragmentation/EOF, AVC conversion/IDR, microphone PCM/encryption, private pairing persistence and HID contract.
- Python protocol/security tests: malformed frames, bounded queues, normalized input allowlist, credential permissions, TLS/Origin/Host/token enforcement, authenticated media/control exchange.
- Identity importer tests use generated synthetic identities: matching/signing, wrong key/hash, absent/duplicate/oversized/symlink ZIP entries, path traversal avoidance, permissions, idempotence and no overwrites.
- JVM integration tests provision a synthetic identity and start actual wireless/USB RTSP listeners. Wired uses IPv6, no AP settings, and exercises real iAP2 startup bytes on the USB pipe; **not iPhone trust or a physical USB session**.
- Four-way frontend/transport dispatch: native and Web both select wired/wireless, with wired never constructing a radio adapter.
- USB fixtures: descriptor-based configuration, selected-phone/NCM correlation (excluding ipheth and unrelated NICs), scoped IPv6/DAD checks, explicit setup consent, partial writes, timeouts/cancellation and bounded queues.
- On CI, load the actual libimobiledevice ABI and reject a deliberately nonexistent phone. This can expose missing library symbols/FFI errors, not USB/iPhone interoperability.
- GTK/GStreamer/Xvfb synthetic H.264/PCM playback, explicit software decoding and silent audio sink. Render counts are decoded-buffer observations, not physical panel latency.
- Six Node.js lifecycle regression tests exercise terminal decoder errors, stale callbacks, stopping and bounded recovery. They use fake codecs and do not claim media playback.
- x86_64 Chromium test renders frames via WebCodecs, enables AudioContext, sends controls, forces a closed-decoder recovery and captures a screenshot. The backend status stays explicitly DEMO.
- Pinned upstream public APK import checks SHA-256, assets and P-256 key/certificate consistency. Only nonsecret metadata is archived; the private files are deleted. This does not prove iOS accepts the identity.
- Packaging excludes accessory credentials, Android keystores, browser tokens and caches.

## Physical acceptance still required

Wired: real data/host port, selected-phone mode/configuration, kernel NCM and IPv6, usbmuxd/Trust/Carkit/TLS. Wireless: AP SSID/channel/password/address; Bluetooth pairing; actual iPhone authentication; Wi-Fi handover; first frame; touch including letterboxing; music; navigation mixing; Siri; calls; reconnects; repeated shutdown/start; long stationary runs. Record exact iOS/build, decoder name, RSS/CPU/temperature and audio glitches.

A factory listing is not hardware decode. An authenticated provider is not a completed CarPlay session. A synthetic demo is not a real phone. No boot-time/FPS/success-percentage claim is made.

Vehicle deployment needs safe mounting, visibility, power conditioning and battery/thermal review beyond software CI. Never debug controls while driving.

## CI test-harness correction

The earlier x86_64 run reached synthetic pixels/audio but failed when a Playwright
string predicate required `eval` under the production CSP. The test now polls a
function through the browser debugger; CSP was not weakened and decoder recovery
is still exercised. Inspect the run for this commit before claiming a pass.
