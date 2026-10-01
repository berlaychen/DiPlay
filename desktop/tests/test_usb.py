"""USB discovery/configuration invariants. Fixtures are NOT physical-iPhone tests."""
import ctypes as C
import io
import json
import os
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from diplay_linux.config import Config
from diplay_linux.usb import (Phone, serial_key, select_phone, configurations,
                              carplay_configuration, ncm_interfaces, link_facts, prepare)
from diplay_linux.usb_helper import Carkit, relay
from diplay_linux.wired import WiredTransport
from diplay_linux.app import Application

UDID = '00008110-001234567890001E'


def iface(number, cls, subclass, protocol=0):
    return bytes([9, 4, number, 0, 2, cls, subclass, protocol, 0])


def config(value, *parts):
    body = b''.join(parts)
    return bytes([9, 2]) + (9 + len(body)).to_bytes(2, 'little') + bytes([len(parts), value, 0, 0x80, 50]) + body


@pytest.mark.parametrize('bad', ['', '../../tmp/key', '0000', 'x'*40, 'a'*65, 'a'*23+'-'])
def test_reject_invalid_udid(bad):
    with pytest.raises(ValueError):
        serial_key(bad)


def test_usb_config_is_descriptor_selected_not_number_six():
    normal = config(4, iface(1, 0xff, 0xfe, 2))
    carplay = config(9, iface(1, 0xff, 0xfe, 2), iface(3, 2, 0x0d), iface(4, 10, 0))
    assert carplay_configuration(normal + carplay)['value'] == 9
    assert carplay_configuration(normal) is None
    assert carplay_configuration(config(6, iface(3, 2, 0x0d))) is None
    assert carplay_configuration(config(6, iface(1, 0xff, 0xfd, 1))) is None
    with pytest.raises(ValueError):
        configurations(carplay[:-1])


def test_cdc_union_and_altsetting():
    raw = config(5, iface(1, 0xff, 0xfe, 2), iface(3, 2, 13), bytes([5, 0x24, 6, 3, 4]), iface(4, 10, 0))
    assert carplay_configuration(raw)['unions'][3] == (4,)


def test_selection_matches_normalized_udid_and_rejects_ambiguity(tmp_path):
    one = Phone(tmp_path/'one', UDID.replace('-', ''), 1, 2)
    two = Phone(tmp_path/'two', 'b'*40, 2, 3)
    assert select_phone([one, two], UDID) == one
    with pytest.raises(RuntimeError, match='Several'):
        select_phone([one, two])
    with pytest.raises(RuntimeError, match='No selected'):
        select_phone([two], UDID)


def add_net(root, device, name, cls=2, sub=13, driver='cdc_ncm', number=3):
    usbintf = device / f'intf{number}'
    usbintf.mkdir(parents=True, exist_ok=True)
    for key, value in [('bInterfaceClass', cls), ('bInterfaceSubClass', sub), ('bInterfaceNumber', number)]:
        (usbintf/key).write_text(f'{value:02x}')
    drivers = root.parent / 'drivers' / driver
    drivers.mkdir(parents=True, exist_ok=True)
    (usbintf/'driver').symlink_to(drivers, target_is_directory=True)
    net = root/name
    net.mkdir(parents=True)
    (net/'device').symlink_to(usbintf, target_is_directory=True)


def test_ncm_must_belong_to_selected_phone_not_ipheth_or_other_phone(tmp_path):
    root = tmp_path/'net'
    selected, other = tmp_path/'selected', tmp_path/'other'
    add_net(root, selected, 'enxcarplay')
    add_net(root, selected, 'usb0', 0xff, 0xfd, 'ipheth', 5)
    add_net(root, other, 'ncm-other')
    phone = Phone(selected, UDID, 1, 2)
    assert ncm_interfaces(phone, root) == [('enxcarplay', 3)]


def record(flags=None, address_flags=None):
    return [dict(ifname='enxcarplay', flags=['UP'] if flags is None else flags,
                 address='02:11:22:33:44:55', addr_info=[dict(family='inet6', local='fe80::1234',
                                                           flags=address_flags or [])])]


def test_scoped_linklocal_and_dad_validation():
    result = link_facts('enxcarplay', 3, record())
    assert result['address'] == 'fe80::1234%enxcarplay'
    assert result['interface_number'] == 3
    for records in (record(flags=[]), record(address_flags=['tentative']),
                    [dict(record()[0], addr_info=[dict(family='inet', local='172.20.10.2')])]):
        with pytest.raises(RuntimeError):
            link_facts('enxcarplay', 3, records)


@pytest.mark.parametrize('mode', ['native', 'web'])
@pytest.mark.parametrize('transport', ['wired', 'wireless'])
def test_both_frontends_select_both_transports_without_cross_dependencies(tmp_path, mode, transport, monkeypatch):
    app = Application.__new__(Application)
    app.config = Config({'connection': {'transport': transport}}, mode)
    app.generation = 0
    app.emit = Mock()
    app.control = Mock()
    import sys
    core, radio, wired = Mock(), Mock(), Mock()
    monkeypatch.setitem(sys.modules, 'diplay_linux.core', SimpleNamespace(Core=core))
    monkeypatch.setitem(sys.modules, 'diplay_linux.radio', SimpleNamespace(Radio=radio))
    monkeypatch.setitem(sys.modules, 'diplay_linux.wired', SimpleNamespace(WiredTransport=wired))
    monkeypatch.setattr(app.config, 'network_settings', Mock(return_value={}))
    monkeypatch.setattr(app.config, 'core_settings', Mock(return_value={}))
    app.start_connection()
    if transport == 'wired':
        wired.assert_called_once()
        wired.return_value.prepare.assert_called_once()
        radio.assert_not_called()
        app.config.network_settings.assert_not_called()
        core.assert_not_called()  # Starts only once NCM has been discovered.
        facts = dict(device_mac='02:11:22:33:44:55')
        app.usb_prepared(facts)
        app.config.core_settings.assert_called_once_with(facts['device_mac'], wired=facts)
    else:
        radio.assert_called_once()
        wired.assert_not_called()
    core.assert_called_once()


def test_wired_config_does_not_require_wifi_bluetooth_or_remote_auth(tmp_path):
    for name in ('identity.pk8', 'certificate.p7b'):
        path = tmp_path/name
        path.write_bytes(b'validated-by-JVM-later')
        path.chmod(0o600)
    cfg = Config({'connection': {'transport': 'wired'}, 'auth': {'directory': str(tmp_path)}})
    facts = dict(udid=UDID, **link_facts('enxcarplay', 3, record()))
    settings = cfg.core_settings(facts['device_mac'], wired=facts)
    assert settings['transport'] == 'wired'
    assert settings['usb_interface_number'] == 3
    assert not set(settings) & {'ssid', 'password', 'phone', 'channel'}
    with pytest.raises(ValueError, match='scope'):
        cfg.core_settings(facts['device_mac'], wired=dict(facts, address='fe80::1234%wrong'))


def test_no_usb_mode_change_without_explicit_consent(tmp_path, monkeypatch):
    phone = Phone(tmp_path, UDID, 1, 2)
    monkeypatch.setattr('diplay_linux.usb.phones', lambda _: [phone])
    monkeypatch.setattr('diplay_linux.usb.read_descriptors', lambda _: config(4, iface(1, 0xff, 0xfe, 2)))
    switch = Mock()
    monkeypatch.setattr('diplay_linux.usb.request_carplay_mode', switch)
    with pytest.raises(RuntimeError, match='--configure'):
        prepare()
    switch.assert_not_called()


def test_carkit_partial_writes_and_receive_timeout():
    client = Carkit.__new__(Carkit)
    client.service = None
    sent = []
    def send(service, data, size, count):
        size = min(size, 2)
        sent.append(data.raw[:size])
        C.cast(count, C.POINTER(C.c_uint32))[0] = size
        return 0
    client.lib = SimpleNamespace(service_send=send,
        service_receive_with_timeout=lambda *args: -7)
    client.send(b'abcdef')
    assert b''.join(sent) == b'abcdef'
    assert client.receive() == b''
    client.lib.service_receive_with_timeout = lambda *args: 0
    with pytest.raises(EOFError):
        client.receive()


def test_usb_helper_is_cancellable_without_owning_main_process():
    transport = WiredTransport({'timeout': 1}, Mock(), Mock())
    process = Mock()
    process.poll.return_value = None
    transport.process = process
    transport.close()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=2)
    assert transport.closed.is_set()


def test_usb_output_backpressure_is_bounded():
    emit = Mock()
    transport = WiredTransport({'timeout': 1}, emit, Mock())
    for _ in range(64):
        transport.write(b'x')
    transport.write(b'x')
    assert transport.failed
    assert transport.outgoing.qsize() == 64
    assert any(call.args[0].get('component') == 'usb' for call in emit.call_args_list)


def test_readonly_prepare_correlates_sysfs_to_usbmuxd(tmp_path, monkeypatch):
    selected = tmp_path/'phone'
    selected.mkdir()
    (selected/'bConfigurationValue').write_text('9')
    (selected/'descriptors').write_bytes(config(9, iface(1, 255, 254, 2), iface(3, 2, 13)))
    phone = Phone(selected, UDID.replace('-', ''), 1, 2)
    net = tmp_path/'net'
    add_net(net, selected, 'enxcarplay')
    monkeypatch.setattr('diplay_linux.usb.phones', lambda _: [phone])
    calls = []
    def output(args, **kwargs):
        calls.append(args)
        return json.dumps(record()).encode() if args[0] == 'ip' else (UDID+'\n').encode()
    monkeypatch.setattr('diplay_linux.usb.subprocess.check_output', output)
    switch = Mock()
    monkeypatch.setattr('diplay_linux.usb.request_carplay_mode', switch)
    facts = prepare(net_root=net, timeout=1)
    assert facts['udid'] == UDID
    assert facts['interface_number'] == 3
    switch.assert_not_called()
    assert all('set' not in call for call in calls)


def test_real_library_rejects_nonexistent_phone_without_segfault():
    from ctypes.util import find_library
    import subprocess
    import sys
    if not find_library('imobiledevice-1.0'):
        pytest.skip('libimobiledevice not installed on this test host; installed in CI')
    result = subprocess.run([sys.executable, '-m', 'diplay_linux.usb_helper', 'connect', 'f'*40],
                            capture_output=True, timeout=10)
    assert result.returncode == 2, result.stderr.decode(errors='replace')
    assert result.stdout == b''
    # Fail if a missing symbol/library caused exit 2 before the first real ABI call.
    assert b'USB iPhone unavailable (libimobiledevice code ' in result.stderr, result.stderr


@pytest.mark.parametrize('op', ['usb_open', 'usb_data', 'usb_closed', 'bt_open', 'bt_data', 'start'])
def test_browser_cannot_inject_transport_commands(op):
    from diplay_linux.protocol import validate_control
    with pytest.raises(ValueError):
        validate_control({'op': op, 'data': b'forbidden'})
