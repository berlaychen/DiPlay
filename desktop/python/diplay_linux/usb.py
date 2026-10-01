"""Linux iPhone/NCM discovery. Never guess an interface from its name.

Preparation is explicit: configuration switching affects only the selected iPhone,
and bringing a link up does not add routes, DNS or a DHCP client. Runtime discovery
is read-only by default. The Carkit/TLS connection lives in usb_helper.py.
"""
from dataclasses import dataclass
import ipaddress
import json
import re
import subprocess
import time
from pathlib import Path

APPLE_VENDOR = 0x05AC
SERIAL = re.compile(r'^[0-9a-fA-F-]{24,64}$')
IFNAME = re.compile(r'^[a-zA-Z0-9_.:-]{1,15}$')
MAC = re.compile(r'^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$')


def serial_key(value):
    if not isinstance(value, str) or not SERIAL.fullmatch(value):
        raise ValueError('Invalid iPhone serial/UDID')
    key = value.replace('-', '').lower()
    if len(key) not in (24, 40):
        raise ValueError('Expected a 24- or 40-digit iPhone identifier')
    return key


def read_small(path, limit=4096):
    with Path(path).open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Unexpectedly large sysfs value')
    return raw.decode('ascii').strip()


@dataclass(frozen=True)
class Phone:
    path: Path
    serial: str
    bus: int
    address: int


def phones(root=Path('/sys/bus/usb/devices')):
    result = []
    for entry in sorted(Path(root).iterdir()) if Path(root).exists() else []:
        try:
            if int(read_small(entry / 'idVendor'), 16) != APPLE_VENDOR:
                continue
            # Do not send mode-switch requests to keyboards, Macs or other Apple devices.
            if not read_small(entry / 'product').startswith('iPhone'):
                continue
            serial = read_small(entry / 'serial')
            serial_key(serial)
            result.append(Phone(entry.resolve(), serial, int(read_small(entry / 'busnum')),
                                int(read_small(entry / 'devnum'))))
        except (OSError, ValueError, UnicodeError):
            continue
    return result


def select_phone(available, udid=''):
    if udid:
        wanted = serial_key(udid)
        available = [phone for phone in available if serial_key(phone.serial) == wanted]
    if not available:
        raise RuntimeError('No selected USB iPhone. Use a data/host port, unlock the phone and reconnect.')
    if len(available) != 1:
        raise RuntimeError('Several USB iPhones are attached; set usb.udid explicitly.')
    return available[0]


def configurations(raw):
    """Parse USB configuration/interface/CDC-union descriptors, including altsettings."""
    if len(raw) > 1024 * 1024:
        raise ValueError('USB descriptors exceed limit')
    output, cfg, offset = [], None, 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            raise ValueError('Truncated USB descriptor')
        length, kind = raw[offset:offset + 2]
        if length < 2 or offset + length > len(raw):
            raise ValueError('Malformed USB descriptor length')
        item = raw[offset:offset + length]
        if kind == 2:
            if length < 9:
                raise ValueError('Short USB configuration')
            cfg = dict(value=item[5], interfaces={}, unions={})
            output.append(cfg)
        elif kind == 4 and cfg is not None:
            if length < 9:
                raise ValueError('Short USB interface')
            if item[3] == 0:
                cfg['interfaces'][item[2]] = tuple(item[5:8])
        elif kind == 0x24 and cfg is not None and length >= 5 and item[2] == 6:
            cfg['unions'][item[3]] = tuple(item[4:])
        offset += length
    return output


def carplay_configuration(raw):
    candidates = [cfg for cfg in configurations(raw)
                  if (0xFF, 0xFE, 2) in cfg['interfaces'].values()
                  and any(kind[:2] == (2, 0x0D) for kind in cfg['interfaces'].values())]
    if not candidates:
        return None
    # Same descriptor preference as the Android implementation, not a hardcoded config 6.
    return sorted(candidates, key=lambda cfg: ((0xFF, 0xFD, 1) not in cfg['interfaces'].values(), cfg['value']))[0]


def ncm_interfaces(phone, net_root=Path('/sys/class/net')):
    result = []
    for entry in sorted(Path(net_root).iterdir()) if Path(net_root).exists() else []:
        if not IFNAME.fullmatch(entry.name):
            continue
        try:
            interface = (entry / 'device').resolve(strict=True)
            if interface.parent != phone.path.resolve():
                continue
            if (interface / 'driver').resolve(strict=True).name != 'cdc_ncm':
                continue
            cls = int(read_small(interface / 'bInterfaceClass'), 16)
            sub = int(read_small(interface / 'bInterfaceSubClass'), 16)
            if (cls, sub) != (2, 0x0D):
                continue
            number = int(read_small(interface / 'bInterfaceNumber'), 16)
            result.append((entry.name, number))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item[1])


def link_facts(interface, interface_number, records):
    if not IFNAME.fullmatch(interface) or not 0 <= interface_number <= 255:
        raise ValueError('Invalid NCM interface')
    matching = [x for x in records if x.get('ifname') == interface]
    if len(matching) != 1:
        raise RuntimeError('Selected NCM network interface disappeared')
    record = matching[0]
    mac = record.get('address', '')
    if not MAC.fullmatch(mac) or mac == '00:00:00:00:00:00':
        raise RuntimeError('NCM has no valid MAC address')
    if 'UP' not in record.get('flags', []):
        raise RuntimeError('NCM link is down; run diplay-usb prepare --bring-up explicitly.')
    for info in record.get('addr_info', []):
        if info.get('family') != 'inet6' or info.get('tentative') or info.get('dadfailed'):
            continue
        if set(info.get('flags', [])) & {'tentative', 'dadfailed'}:
            continue
        ip = ipaddress.ip_address(info.get('local', '::'))
        if ip.is_link_local:
            return dict(interface=interface, interface_number=interface_number,
                        address=str(ip) + '%' + interface, device_mac=mac.upper())
    raise RuntimeError('NCM needs a non-tentative IPv6 link-local address; check IPv6 and link state.')


def read_descriptors(phone):
    with (phone.path / 'descriptors').open('rb') as source:
        return source.read(1024 * 1024 + 1)


def request_carplay_mode(phone):
    """Apple's existing mode-4 request, scoped by bus/address AND rechecked serial.

    See upstream IphoneUsbHost and usbmuxd APPLE_VEND_SPECIFIC_SET_MODE.
    No firmware is written; the phone can disconnect/re-enumerate.
    """
    try:
        import usb.core
        import usb.util
    except ImportError as error:
        raise RuntimeError('USB preparation needs python3-usb (PyUSB)') from error
    device = usb.core.find(idVendor=APPLE_VENDOR, bus=phone.bus, address=phone.address)
    if device is None:
        raise RuntimeError('Selected USB phone disappeared before mode switch')
    try:
        if serial_key(usb.util.get_string(device, device.iSerialNumber)) != serial_key(phone.serial):
            raise RuntimeError('USB identity changed; refusing mode switch')
        try:
            response = device.ctrl_transfer(0xC0, 0x52, 0, 4, 1, timeout=1500)
            if len(response) != 1:
                raise RuntimeError('Unexpected CarPlay mode-switch response')
        except usb.core.USBError as error:
            # A successful request can remove the device before the status stage.
            if error.errno not in (19,):
                raise RuntimeError('CarPlay USB mode switch failed; check selected-device permissions') from error
    finally:
        usb.util.dispose_resources(device)


def prepare(udid='', interface='', configure=False, bring_up=False, timeout=30,
            usb_root=Path('/sys/bus/usb/devices'), net_root=Path('/sys/class/net')):
    """Discover and optionally prepare one phone; never start a competing usbmuxd."""
    deadline = time.monotonic() + timeout
    phone = select_phone(phones(usb_root), udid)
    selected = phone.serial
    cfg = carplay_configuration(read_descriptors(phone))
    if cfg is None and not configure:
        raise RuntimeError('iPhone is not in USBMUX + CDC-NCM mode. Run diplay-usb prepare --configure --bring-up.')
    if cfg is None:
        request_carplay_mode(phone)
    config_written, up_written, last = False, False, 'Waiting for the selected iPhone to re-enumerate'
    while time.monotonic() < deadline:
        try:
            phone = select_phone(phones(usb_root), selected)
            cfg = carplay_configuration(read_descriptors(phone))
            if cfg is None:
                raise RuntimeError('Selected iPhone has no USBMUX + CDC-NCM configuration yet')
            current = int(read_small(phone.path / 'bConfigurationValue'))
            if current != cfg['value']:
                if not configure:
                    raise RuntimeError('CarPlay USB configuration is not selected; explicit --configure is required')
                if not config_written:
                    (phone.path / 'bConfigurationValue').write_text(str(cfg['value']))
                    config_written = True
                raise RuntimeError('Waiting for usbmuxd/kernel to retain the CarPlay configuration')
            links = ncm_interfaces(phone, net_root)
            if interface:
                links = [link for link in links if link[0] == interface]
            if not links:
                raise RuntimeError('Selected iPhone has no cdc_ncm network link; load cdc_ncm and check USB configuration')
            name, number = links[0]
            if bring_up and not up_written:
                subprocess.run(['ip', 'link', 'set', 'dev', name, 'up'], check=True,
                               capture_output=True, timeout=5)
                up_written = True
            raw = subprocess.check_output(['ip', '-j', 'address', 'show', 'dev', name], timeout=5)
            facts = link_facts(name, number, json.loads(raw))
            # libimobiledevice uses an inserted dash on recent iPhone UDIDs. Ask usbmuxd,
            # then correlate rather than passing the sysfs spelling blindly.
            ids = subprocess.check_output(['idevice_id', '-l'], timeout=5).decode('ascii').splitlines()
            matches = [x for x in ids if serial_key(x) == serial_key(selected)]
            if len(matches) != 1:
                raise RuntimeError('usbmuxd has not discovered the selected USB iPhone; check its service/permissions')
            return dict(facts, udid=matches[0])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if isinstance(error, PermissionError):
                raise RuntimeError('USB setup requires permission on the selected device/link; do not run the GUI as root') from error
            last = str(error)
            time.sleep(0.25)
    raise RuntimeError('USB preparation timed out: ' + last)
