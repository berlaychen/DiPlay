#!/bin/sh
# Explicit opt-in only. This can disconnect the selected interface's normal Wi-Fi.
set -eu
if [ "$#" -ne 2 ] || [ "$2" != '--create-dedicated-ap' ]; then
  echo 'Usage: setup-ap.sh CONFIG.toml --create-dedicated-ap' >&2
  echo 'Disconnects Wi-Fi on that interface. Use local console/Ethernet for administration.' >&2
  exit 2
fi
exec /usr/bin/python3 - "$1" <<'PY'
import re, subprocess, sys, tomllib
from pathlib import Path
cfg = tomllib.loads(Path(sys.argv[1]).read_text())['network']
interface, ssid, password = cfg['interface'], cfg['ssid'], cfg['password']
channel = cfg.get('channel', 36)
if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', interface): raise SystemExit('Invalid interface')
if not 0 < len(ssid.encode()) <= 32 or '\0' in ssid: raise SystemExit('Invalid SSID')
if not 8 <= len(password) <= 63 or not password.isascii() or '\0' in password: raise SystemExit('Invalid password')
if type(channel) is not int or not 1 <= channel <= 196: raise SystemExit('Invalid channel')
name = 'diplay-ap-' + interface
result = subprocess.run(['nmcli', '-g', 'NAME', 'connection', 'show'], capture_output=True, text=True, check=True)
if name in result.stdout.splitlines():
    raise SystemExit('Profile exists. Inspect with nmcli; this helper will not overwrite it.')
command = ['nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', interface, 'con-name', name,
           'ssid', ssid, '802-11-wireless.mode', 'ap',
           '802-11-wireless.band', 'bg' if channel <= 14 else 'a',
           '802-11-wireless.channel', str(channel), 'wifi-sec.key-mgmt', 'wpa-psk',
           'wifi-sec.proto', 'rsn', 'wifi-sec.psk', password,
           'ipv4.method', 'shared', 'ipv4.addresses', '10.42.0.1/24',
           'ipv6.method', 'link-local', 'connection.autoconnect', 'no']
subprocess.run(command, check=True)
subprocess.run(['nmcli', 'connection', 'up', name], check=True)
print('Verify actual AP channel: iw dev', interface, 'info')
print('Cleanup: nmcli connection down', name)
print('         nmcli connection delete', name)
PY
