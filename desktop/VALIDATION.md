# Validation scope

The workflow `Linux native and Web` runs on generic x86_64 and ARM64 Ubuntu hosts, **not T100/Pi hardware**. Inspect the current run; this document defines checks, not a blanket pass claim.

## Automated

- Compile selected Kotlin/JVM code without Android SDK/runtime.
- 9 core self-tests: IPC roundtrip/size/truncation, RFCOMM fragmentation/EOF, AVC conversion/IDR, microphone PCM/encryption, private pairing persistence and HID contract.
- Python protocol/security tests: malformed frames, bounded queues, normalized input allowlist, credential permissions, TLS/Origin/Host/token enforcement, authenticated media/control exchange.
- Identity importer tests use generated synthetic identities: matching/signing, wrong key/hash, absent/duplicate/oversized/symlink ZIP entries, path traversal avoidance, permissions, idempotence and no overwrites.
- JVM integration test provisions a synthetic identity and starts the actual RTSP listener. It verifies a response and clean shutdown; **not iPhone trust**.
- GTK/GStreamer/Xvfb synthetic H.264/PCM playback, explicit software decoding and silent audio sink. Render counts are decoded-buffer observations, not physical panel latency.
- x86_64 Chromium test renders frames via WebCodecs, enables AudioContext, sends controls and captures a screenshot. The backend status stays explicitly DEMO.
- Pinned upstream public APK import checks SHA-256, assets and P-256 key/certificate consistency. Only nonsecret metadata is archived; the private files are deleted. This does not prove iOS accepts the identity.
- Packaging excludes accessory credentials, Android keystores, browser tokens and caches.

## Physical acceptance still required

AP SSID/channel/password/address; Bluetooth pairing; actual iPhone authentication; Wi-Fi handover; first frame; touch including letterboxing; music; navigation mixing; Siri; calls; reconnects; repeated shutdown/start; long stationary runs. Record exact iOS/build, decoder name, RSS/CPU/temperature and audio glitches.

A factory listing is not hardware decode. An authenticated provider is not a completed CarPlay session. A synthetic demo is not a real phone. No boot-time/FPS/success-percentage claim is made.

Vehicle deployment needs safe mounting, visibility, power conditioning and battery/thermal review beyond software CI. Never debug controls while driving.
