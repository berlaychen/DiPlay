# Linux wired CarPlay

Both `--mode native` and `--mode web` use this same wired adapter. Select it with
`--transport wired` or `[connection] transport="wired"`. The wireless adapter remains
available to both frontends. These choices are independent; neither frontend embeds
an Android runtime.

## What is implemented

```text
iPhone USB data connection
  |-- usbmuxd -> libimobiledevice Lockdown -> com.apple.carkit.service (TLS as requested)
  |                                               |
  |                                      isolated stdio helper
  |                                               |
  |                                   shared Kotlin iAP2/authentication
  |
  `-- Linux cdc_ncm -> scoped IPv6 link-local -> shared AirPlay/media engine
                                                    |             |
                                               GTK/GStreamer   WebSocket/browser
```

The code uses real libimobiledevice APIs, not a fake CarPlay protocol. The existing
DiPlay local/remote authentication provider is shared across both transports and
both frontends. It does not substitute USB Trust for accessory authentication.

Discovery correlates **the selected iPhone serial/UDID, USB descriptors, kernel
driver and network interface**. `usb0`, `enx*`, an existing Ethernet link or iPhone
`ipheth` Personal Hotspot alone are not accepted as a CarPlay NCM interface. The
USB control-interface number comes from descriptors/sysfs, not a hardcoded number.
Multiple attached iPhones require an explicit `usb.udid`.

The helper is an isolated process, so an unresponsive trust dialog/TLS call can be
cancelled. Its stdout is exclusively iAP2 bytes in connect mode; stderr contains
only bounded status/errors. TLS reads/writes happen on one thread. IPC/output
queues and startup timeouts are bounded. Reconnect replaces transport and protocol
state without replacing either frontend; generation checks discard old callbacks.

## Setup on T100 / Pi 4 / Pi 5

Use a real USB **host/data** port and a data cable. For a T100TA, the keyboard dock's
USB-A port is the initial test target; do not assume the tablet's charging socket
supports this host role. A powered hub may be necessary for power integrity, but
must not back-feed either device.

Install the normal desktop dependencies, including `usbmuxd`,
`libimobiledevice-utils` and `python3-usb`:

```sh
./desktop/install-deps.sh
./desktop/diplay-usb list
```

The list command is read-only. Unlock the iPhone and keep it attached. When the
phone already exposes a configured NCM interface, this checks it without changes:

```sh
./desktop/diplay-usb prepare
```

On Linux, unlike LoopLink's macOS setup, the OS may not select CarPlay USB mode
for you. The following **explicit** preparation permits changing only the selected
iPhone's USB mode/configuration and bringing its NCM link up:

```sh
sudo modprobe cdc_ncm
sudo ./desktop/diplay-usb prepare --configure --bring-up
# With several attached iPhones, add --udid THE_SELECTED_IPHONE_UDID.
```

This can disconnect/re-enumerate that iPhone and interrupt its current USB uses.
It does not flash firmware, stop system services, detach unrelated devices, change
firewalls, or add default routes/DNS. It may be required again after unplugging or
rebooting; this is not promised as one-time permanent setup. Never run the whole
GUI/browser backend as root. Do not disable USB security globally.

The mode request follows upstream's Apple vendor request `0x52`, mode index `4`.
Configuration selection is descriptor-based (USBMUX + CDC-NCM), not always `6`.
The kernel and usbmuxd must retain that configuration. If your usbmuxd repeatedly
changes it, inspect its version/device-mode configuration instead of running a
second daemon or repeatedly forcing the device. Kernel support, USB permissions,
IPv6, a data cable, and iPhone Trust remain deployment requirements.

Copy the wired profile, provision authentication as in the main README, then run
as your ordinary desktop user:

```sh
mkdir -p ~/.config/diplay
cp desktop/profiles/t100-wired.toml ~/.config/diplay/config.toml
# Pi: choose rpi4-wired.toml or rpi5-wired.toml instead.
chmod 600 ~/.config/diplay/config.toml

./desktop/diplay --mode native --transport wired --config ~/.config/diplay/config.toml
# OR, not simultaneously on the same phone/port:
./desktop/diplay --mode web --transport wired --config ~/.config/diplay/config.toml
```

The Web browser connects to the Linux backend, **not directly to the USB device**.
USB selection and secrets stay on Linux. Remote browser access uses the same
TLS/Origin/token requirements as wireless mode.

Accept the iPhone Trust/CarPlay prompts. If it times out, correct the indicated
problem, then press **Reconnect**. `waiting_for_usb_trust`, `authenticated`,
`connected`, and actual rendered video are distinct states. A trust record alone
is not a successful CarPlay session.

## Configuration

```toml
[connection]
transport="wired"
[usb]
udid=""             # auto only if exactly one iPhone is attached
interface=""        # auto; override is still checked against that iPhone
timeout=30          # 1..120 seconds
configure=false     # runtime read-only by default
bring_up=false      # use explicit preparation instead
```

No `[network]` block,
Bluetooth MAC, Wi-Fi AP or Internet connection is required in local-auth wired
mode. `--doctor --transport wired` performs read-only capability discovery. It
does not request Trust or claim that a real phone session works.

## Evidence and remaining validation

Automated checks cover both frontend/transport combinations; synthetic USB
configuration/NCM fixtures; phone correlation; permission/consent boundaries;
partial writes and cancellation; real JVM IPv6 listener and USB iAP2 output;
and loading the real libimobiledevice ABI with an intentionally nonexistent phone.

**No physical iPhone/T100/Pi USB CarPlay connection has been exercised by these
tests.** Device re-enumeration, retained NCM configuration, iPhone certificate
acceptance, TLS interoperability, video/audio, Siri/calls and cable reconnection
must still be tested together on your hardware. Implementation is not a claim of
hardware validation. Wireless has the same physical-validation limitation.

References consulted: LoopLink `carplay-browser`'s isolated Carkit helper and
upstream libimobiledevice `service.h` / `libimobiledevice.h`, usbmuxd's mode/config
logic, and DiPlay's `IphoneUsbHost` / `IphoneCarPlayConfiguration`.
