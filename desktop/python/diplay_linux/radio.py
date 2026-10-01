"""BlueZ RFCOMM and Avahi adapters. All D-Bus callbacks run in the GLib main loop.

The operator owns pairing and AP provisioning. No adapter is powered/reset and no
unknown device is trusted automatically. The runtime requires no root shell.
"""
import ipaddress
import logging
import queue
import socket
import threading
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

IAP2_UUID = '00000000-deca-fade-deca-deafdecacafe'
PROFILE = '/dev/diplay/profile'


class Profile(dbus.service.Object):
    def __init__(self, bus, owner):
        super().__init__(bus, PROFILE)
        self.owner = owner

    @dbus.service.method('org.bluez.Profile1', in_signature='', out_signature='')
    def Release(self):
        self.owner.release_socket()

    @dbus.service.method('org.bluez.Profile1', in_signature='oha{sv}', out_signature='')
    def NewConnection(self, device, fd, properties):
        descriptor = fd.take()
        conn = socket.socket(fileno=descriptor)
        if str(device) != self.owner.device_path or self.owner.socket is not None:
            conn.close()
            return
        self.owner.accept(conn)

    @dbus.service.method('org.bluez.Profile1', in_signature='o', out_signature='')
    def RequestDisconnection(self, device):
        if str(device) == self.owner.device_path:
            self.owner.release_socket()


class Radio:
    def __init__(self, network, emit, send):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.network, self.emit, self.send = network, emit, send
        self.socket = None
        self.closed = False
        self.profile = None
        self.registered = False
        self.group = None
        self.browser = None
        self.signals = []
        self.last_probe = {}
        self.probe_pending = set()
        self.probe_lock = threading.Lock()
        self.pending_connect = False
        self.handed_off = False
        self.device_id = ''
        self.address = network['address']
        self.interface_index = socket.if_nametoindex(network['interface'])
        self.tx = queue.Queue(128)
        self.generation = 0
        objects = dbus.Interface(self.bus.get_object('org.bluez', '/'),
                                 'org.freedesktop.DBus.ObjectManager').GetManagedObjects()
        adapter_name = network.get('bluetooth_adapter', 'hci0')
        adapter_path = '/org/bluez/' + adapter_name
        adapter = objects.get(adapter_path, {}).get('org.bluez.Adapter1')
        if not adapter or not adapter.get('Powered'):
            raise RuntimeError('Selected Bluetooth adapter is unavailable or not powered')
        self.bt_mac = str(adapter['Address'])
        self.device_path = adapter_path + '/dev_' + network['phone'].upper().replace(':', '_')
        device = objects.get(self.device_path, {}).get('org.bluez.Device1')
        if not device or not device.get('Paired'):
            raise RuntimeError('Pair the iPhone using bluetoothctl or desktop Bluetooth settings first')
        self.device = dbus.Interface(self.bus.get_object('org.bluez', self.device_path), 'org.bluez.Device1')
        self.manager = dbus.Interface(self.bus.get_object('org.bluez', '/org/bluez'), 'org.bluez.ProfileManager1')
        self.avahi = dbus.Interface(self.bus.get_object('org.freedesktop.Avahi', '/'), 'org.freedesktop.Avahi.Server')

    def start(self, ready):
        self.device_id = ready['txt']['deviceid'].replace(':', '')
        self.profile = Profile(self.bus, self)
        self.manager.RegisterProfile(PROFILE, IAP2_UUID, {
            'Name': 'DiPlay Linux iAP2', 'Role': 'client',
            'RequireAuthentication': dbus.Boolean(True),
            'RequireAuthorization': dbus.Boolean(False),
            'AutoConnect': dbus.Boolean(True),
        })
        self.registered = True
        group_path = self.avahi.EntryGroupNew()
        self.group = dbus.Interface(self.bus.get_object('org.freedesktop.Avahi', group_path),
                                    'org.freedesktop.Avahi.EntryGroup')
        # Restrict discovery to the exact interface/family used by the AirPlay listener.
        protocol = 1 if ':' in self.address else 0  # AVAHI_PROTO_INET6 / INET
        txt = dbus.Array([dbus.ByteArray((str(k) + '=' + str(v)).encode())
                          for k, v in ready['txt'].items()], signature='ay')
        self.group.AddService(dbus.Int32(self.interface_index), dbus.Int32(protocol), dbus.UInt32(0),
                              ready['name'], '_airplay._tcp', '', '', dbus.UInt16(ready['port']), txt)
        self.group.Commit()
        browser_path = self.avahi.ServiceBrowserNew(self.interface_index, protocol,
                                                    '_carplay-ctrl._tcp', 'local', 0)
        self.browser = dbus.Interface(self.bus.get_object('org.freedesktop.Avahi', browser_path),
                                      'org.freedesktop.Avahi.ServiceBrowser')
        self.signals.append(self.browser.connect_to_signal('ItemNew', self._found_service))
        self.connect()

    def _found_service(self, interface, protocol, name, service, domain, flags):
        if self.closed or int(interface) != self.interface_index:
            return
        self.avahi.ResolveService(interface, protocol, name, service, domain, protocol, 0,
                                  reply_handler=self._resolved, error_handler=lambda e: None)

    def _resolved(self, interface, protocol, name, service, domain, host, address_protocol, address, port, txt, flags):
        if self.closed:
            return
        # Only probe the selected phone's advertised identity when provided.
        fields = {}
        for field in txt:
            key, sep, value = bytes(field).partition(b'=')
            if sep:
                fields[key.decode(errors='replace')] = value.decode(errors='replace')
        identity = fields.get('id', '').upper().replace(':', '')
        selected = self.network['phone'].upper().replace(':', '')
        if identity and identity != selected:
            return
        key = (str(address), int(port))
        with self.probe_lock:
            if key in self.probe_pending or time.monotonic() - self.last_probe.get(key, -100) < 3:
                return
            if len(self.probe_pending) >= 4:
                return
            self.probe_pending.add(key)
        threading.Thread(target=self._probe, args=key, daemon=True).start()

    def _probe(self, address, port):
        try:
            ip = ipaddress.ip_address(address.split('%')[0])
            family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as conn:
                conn.settimeout(3)
                if ip.version == 6:
                    source = self.address.split('%')[0]
                    conn.bind((source, 0, 0, self.interface_index if source.startswith('fe80:') else 0))
                    target = (str(ip), port, 0, self.interface_index if ip.is_link_local else 0)
                    host = f'[{ip}]:{port}'
                else:
                    conn.bind((self.address, 0)); target = (str(ip), port); host = f'{ip}:{port}'
                conn.connect(target)
                request = (f'GET /ctrl-int/1/connect HTTP/1.1\r\nHost: {host}\r\n'
                           'User-Agent: AirPlay/950.7.1\r\n'
                           f'AirPlay-Receiver-Device-ID: {self.device_id}\r\nConnection: close\r\n\r\n')
                conn.sendall(request.encode('ascii'))
                response = conn.recv(1024).split(b'\r\n', 1)[0]
                self.emit(dict(event='diagnostic', component='bonjour',
                               message='iPhone connect probe returned ' + response[:80].decode(errors='replace')))
        except OSError as error:
            self.emit(dict(event='diagnostic', component='bonjour', message=type(error).__name__))
        finally:
            with self.probe_lock:
                self.probe_pending.discard((address, port))
                if len(self.last_probe) >= 32:
                    self.last_probe.clear()
                self.last_probe[(address, port)] = time.monotonic()

    def connect(self):
        if self.closed or self.socket is not None or self.pending_connect or self.handed_off:
            return False
        self.pending_connect = True
        self.device.ConnectProfile(IAP2_UUID, reply_handler=self._connect_ok,
                                   error_handler=self._connect_failed, timeout=25)
        return False

    def _connect_ok(self):
        self.pending_connect = False

    def _connect_failed(self, error):
        self.pending_connect = False
        if not self.closed:
            self.emit(dict(event='diagnostic', component='bluetooth',
                           message='RFCOMM connect failed; unlock iPhone and use Reconnect'))

    def accept(self, conn):
        self.generation += 1
        generation = self.generation
        self.socket = conn
        self.tx = queue.Queue(128)
        self.send(dict(op='bt_open'))
        threading.Thread(target=self._read_socket, args=(conn, generation), daemon=True).start()
        threading.Thread(target=self._write_socket, args=(conn, self.tx), daemon=True).start()

    def _read_socket(self, conn, generation):
        try:
            while not self.closed:
                data = conn.recv(8192)
                if not data:
                    break
                self.send(dict(op='bt_data', data=data))
        except OSError:
            pass
        finally:
            GLib.idle_add(self._socket_ended, generation)

    def _socket_ended(self, generation):
        if generation == self.generation:
            self.release_socket()
        return False

    def _write_socket(self, conn, tx):
        try:
            while not self.closed:
                data = tx.get()
                if data is None:
                    return
                conn.sendall(data)
        except OSError:
            pass

    def write(self, data):
        if self.socket is None:
            return
        try:
            self.tx.put_nowait(data)
        except queue.Full:
            self.emit(dict(event='error', component='bluetooth', message='RFCOMM output overflow'))
            self.release_socket()

    def release_socket(self, handoff=False):
        self.handed_off = handoff
        conn, self.socket = self.socket, None
        self.generation += 1
        try:
            self.tx.put_nowait(None)
        except queue.Full:
            pass
        if conn:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            if not handoff:
                self.send(dict(op='bt_closed'))

    def reconnect(self):
        self.release_socket()
        GLib.timeout_add(1000, self.connect)

    def close(self):
        self.closed = True
        self.release_socket()
        for signal in self.signals:
            signal.remove()
        for obj, method in ((self.browser, 'Free'), (self.group, 'Free')):
            if obj:
                try:
                    getattr(obj, method)()
                except dbus.DBusException:
                    pass
        if self.registered:
            try:
                self.manager.UnregisterProfile(PROFILE)
            except dbus.DBusException:
                pass
        if self.profile:
            self.profile.remove_from_connection()
