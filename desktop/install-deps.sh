#!/bin/sh
# Dependency installation only: no pairing, AP, firewall or startup changes.
set -eu
if ! command -v apt-get >/dev/null; then
  echo 'Use an apt-based distribution or install dependency equivalents from README.' >&2
  exit 2
fi
sudo apt-get update
sudo apt-get install --no-install-recommends \
  default-jre-headless python3 python3-gi python3-dbus python3-aiohttp python3-cryptography \
  gir1.2-gtk-3.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-gtk3 gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  pipewire pipewire-pulse wireplumber bluez avahi-daemon avahi-utils \
  network-manager iw iproute2 ffmpeg usbmuxd libimobiledevice-utils python3-usb
printf '\nDependencies installed. Provision authentication and either USB/NCM or a paired iPhone/AP before live use.\n'
