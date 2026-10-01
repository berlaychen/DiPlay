# Notices

The protocol is reused from DiPlay and xcertplay under the repository GPL-3.0 notices. Preserve LICENSE, docs/THIRD_PARTY_NOTICES.md and docs/licenses, including upstream's DiAuto AGPL-3.0 notices. The Linux frontends are newly written and do not copy Android UI assets.

Kotlin/JVM: JetBrains Apache-2.0; Bouncy Castle: its permissive license; preserve JAR notices. GTK/GStreamer/aiohttp/cryptography/FFmpeg retain their own licenses. Distro dependencies are installed separately. Demo media is synthetic.

Accessory identity data has separate provisioning/authorization concerns. The public source and CI packages do not include it. The explicit local importer supports the identity in upstream's experimental APK without publishing the private key. A private runtime bundle is for personal deployment, not public redistribution. Source availability and key matching are not Apple trust, certification or a statement of licensing rights.

No Apple/BYD affiliation or endorsement. Do not commit private keys, pairing records, tokens or personal logs.

Low-resource integration design reference: youcci/playport at
9a0882dd0ffe48e467b59d58b12d81391df55ade (GPL-3.0), especially its supervisor,
presentation panel, per-stream gains and input controller. The new Linux policy
and input implementation is independently written, retaining immediate edges,
bounded retries and existing Linux security/media boundaries. No PlayPort assets,
private identity, macOS helper or third-party binaries were imported.
