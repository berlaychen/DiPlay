"""Validated deployment settings. No implicit networking changes or credential discovery."""
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import json
import os
import re
import socket
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[2]
MAC = re.compile(r'^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$')
IFACE = re.compile(r'^[a-zA-Z0-9_.:-]{1,15}$')


@dataclass
class Config:
    document: dict
    mode: str = 'native'

    def __post_init__(self):
        if self.mode not in ('native', 'web'):
            raise ValueError('mode must be native or web')
        video = self.document.setdefault('video', {})
        for key, default, low, high in (('width', 960, 320, 1920), ('height', 540, 240, 1200)):
            value = video.setdefault(key, default)
            if type(value) is not int or value % 2 or not low <= value <= high:
                raise ValueError(f'video.{key} must be an even integer in {low}..{high}')
        if video.setdefault('fps', 30) not in (24, 25, 30, 60):
            raise ValueError('video.fps must be 24, 25, 30 or 60')
        decoder = video.setdefault('decoder', 'auto')
        if decoder not in ('auto', 'vah264dec', 'vaapih264dec', 'v4l2h264dec',
                           'v4l2slh264dec', 'avdec_h264'):
            raise ValueError('Unsupported decoder; pipeline text is not accepted')
        web = self.document.setdefault('web', {})
        host = web.setdefault('host', '127.0.0.1')
        address = ipaddress.ip_address(host)
        if not address.is_loopback and not (web.get('tls_certificate') and web.get('tls_key') and web.get('allowed_origin')):
            raise ValueError('Non-loopback web listeners require TLS certificate, key and allowed_origin')
        if type(web.setdefault('port', 8765)) is not int or not 1024 <= web['port'] <= 65535:
            raise ValueError('web.port must be in 1024..65535')
        if web.get('allowed_origin'):
            from urllib.parse import urlsplit
            parsed = urlsplit(web['allowed_origin'])
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path:
                raise ValueError('allowed_origin must be a scheme://host:port origin without a path')
        self.document.setdefault('audio', {}).setdefault('microphone', False)

    @classmethod
    def read(cls, filename, mode):
        return cls(tomllib.loads(Path(filename).read_text()), mode)

    @property
    def state_dir(self):
        base = os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))
        return Path(self.document.get('state_dir', str(Path(base) / 'diplay'))).expanduser()

    def prepare_private_state(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)

    def network_settings(self):
        net = self.document.get('network', {})
        iface = net.get('interface', '')
        if not IFACE.fullmatch(iface):
            raise ValueError('Set network.interface to the dedicated AP interface')
        phone = net.get('phone', '')
        if not MAC.fullmatch(phone):
            raise ValueError('Set network.phone to the paired iPhone Bluetooth address')
        if not net.get('ssid') or len(net['ssid'].encode()) > 32 or '\0' in net['ssid']:
            raise ValueError('Set network.ssid to the existing AP SSID (1..32 bytes)')
        password = net.get('password', '')
        if not 8 <= len(password) <= 63 or not password.isascii() or '\0' in password:
            raise ValueError('Set an ASCII WPA2 AP password of 8..63 characters')
        channel = net.get('channel', 36)
        if type(channel) is not int or not 1 <= channel <= 196:
            raise ValueError('Invalid Wi-Fi channel')
        # AP is prepared by the operator; advertised SSID/channel must match it.
        bind = net.get('address') or interface_address(iface)
        ipaddress.ip_address(bind.split('%')[0])
        if ':' in bind and bind.startswith('fe80:') and '%' not in bind:
            bind += '%' + iface
        return dict(net, address=bind, channel=channel)

    def core_settings(self, bt_mac):
        net = self.network_settings()
        auth = self.document.get('auth', {})
        mode = auth.get('mode', 'local')
        if mode == 'local':
            directory = Path(auth.get('directory', '~/.config/diplay/identity')).expanduser()
            for name in ('identity.pk8', 'certificate.p7b'):
                path = directory / name
                if not path.is_file() or not 0 < path.stat().st_size <= 16384:
                    raise ValueError(f'Authentication is not provisioned: missing/invalid {name}')
                if path.stat().st_mode & 0o077:
                    raise ValueError(f'Authentication file {name} must be owner-only (chmod 600)')
        elif mode == 'remote':
            from urllib.parse import urlsplit
            url = urlsplit(auth.get('url', ''))
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError('Invalid remote authentication URL')
            if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('Remote authentication must use HTTPS (HTTP only on loopback)')
            directory = Path('.')
        else:
            raise ValueError('auth.mode must be local or remote')
        token_file = auth.get('token_file')
        token = ''
        if token_file:
            path = Path(token_file).expanduser()
            if path.stat().st_mode & 0o077:
                raise ValueError('Remote token must be in an owner-only file')
            token = path.read_text().strip()
        video = self.document['video']
        return dict(state_dir=str(self.state_dir), width=video['width'], height=video['height'],
                    fps=video['fps'], bt_mac=bt_mac, address=net['address'],
                    name=self.document.get('name', 'DiPlay Linux'),
                    width_mm=video.get('width_mm', 200), height_mm=video.get('height_mm', 113),
                    airplay_port=net.get('airplay_port', 7000),
                    microphone=self.document['audio']['microphone'],
                    auth_mode=mode, auth_dir=str(directory), auth_url=auth.get('url', ''),
                    auth_token=token, ssid=net['ssid'], password=net['password'], channel=net['channel'])


def interface_address(interface):
    raw = subprocess.check_output(['ip', '-j', 'address', 'show', 'dev', interface], timeout=5)
    entries = json.loads(raw)
    for family in ('inet', 'inet6'):
        for device in entries:
            for address in device.get('addr_info', []):
                if address.get('family') == family and address.get('scope') != 'host':
                    return address['local']
    raise ValueError('AP interface has no address. Configure the AP before starting DiPlay.')
