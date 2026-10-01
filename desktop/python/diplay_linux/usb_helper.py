"""Isolated libimobiledevice Carkit bridge. stdout is raw iAP2 in connect mode.

libimobiledevice owns usbmuxd/Lockdown/TLS; the kernel owns CDC-NCM.
The owner kills this process to cancel a blocked trust dialog or TLS call.
A single thread does both TLS directions. No accessory keys are read here.
"""
import argparse
import ctypes as C
from ctypes.util import find_library
import json
import os
import select
import sys
from .usb import phones, prepare, serial_key


class Carkit:
    def __init__(self, udid):
        serial_key(udid)
        name = find_library('imobiledevice-1.0')
        if not name:
            raise RuntimeError('libimobiledevice is missing; install libimobiledevice-utils and usbmuxd')
        self.lib = C.CDLL(name)
        self.device = C.c_void_p()
        self.lockdown = C.c_void_p()
        self.descriptor = C.c_void_p()
        self.service = C.c_void_p()
        pointer = C.POINTER(C.c_void_p)
        signatures = {
            'idevice_new_with_options': [pointer, C.c_char_p, C.c_int],
            'lockdownd_client_new_with_handshake': [C.c_void_p, pointer, C.c_char_p],
            'lockdownd_start_service': [C.c_void_p, C.c_char_p, pointer],
            'service_client_new': [C.c_void_p, C.c_void_p, pointer],
            'service_send': [C.c_void_p, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)],
            'service_receive_with_timeout': [C.c_void_p, C.c_void_p, C.c_uint32,
                                             C.POINTER(C.c_uint32), C.c_uint],
            'service_client_free': [C.c_void_p],
            'lockdownd_service_descriptor_free': [C.c_void_p],
            'lockdownd_client_free': [C.c_void_p],
            'idevice_free': [C.c_void_p],
        }
        for function, args in signatures.items():
            symbol = getattr(self.lib, function)
            symbol.argtypes, symbol.restype = args, C.c_int
        try:
            self._ok(self.lib.idevice_new_with_options(C.byref(self.device), udid.encode('ascii'), 2),
                     'USB iPhone unavailable')  # IDEVICE_LOOKUP_USBMUX (1 << 1)
            self._ok(self.lib.lockdownd_client_new_with_handshake(self.device, C.byref(self.lockdown), b'diplay-linux'),
                     'Unlock iPhone and approve Trust This Computer')
            self._ok(self.lib.lockdownd_start_service(self.lockdown, b'com.apple.carkit.service', C.byref(self.descriptor)),
                     'iPhone did not expose com.apple.carkit.service')
            self._ok(self.lib.service_client_new(self.device, self.descriptor, C.byref(self.service)),
                     'Could not open the Carkit TLS connection')
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _ok(code, message):
        if code != 0:
            raise RuntimeError(f'{message} (libimobiledevice code {code})')

    def send(self, data):
        offset = 0
        while offset < len(data):
            chunk = data[offset:]
            buffer, sent = C.create_string_buffer(chunk), C.c_uint32()
            self._ok(self.lib.service_send(self.service, buffer, len(chunk), C.byref(sent)), 'Carkit write failed')
            if not 0 < sent.value <= len(chunk):
                raise RuntimeError('Invalid Carkit partial write')
            offset += sent.value

    def receive(self):
        buffer, count = C.create_string_buffer(16384), C.c_uint32()
        code = self.lib.service_receive_with_timeout(self.service, buffer, len(buffer), C.byref(count), 10)
        if code not in (0, -7):  # SERVICE_E_TIMEOUT
            self._ok(code, 'Carkit channel closed')
        if count.value > len(buffer):
            raise RuntimeError('Invalid Carkit read length')
        if code == 0 and count.value == 0:
            raise EOFError('Carkit EOF')
        return buffer.raw[:count.value]

    def close(self):
        for attribute, function in [('service', 'service_client_free'),
                                    ('descriptor', 'lockdownd_service_descriptor_free'),
                                    ('lockdown', 'lockdownd_client_free'), ('device', 'idevice_free')]:
            value = getattr(self, attribute, None)
            if value:
                getattr(self.lib, function)(value)
                setattr(self, attribute, C.c_void_p())


def relay(client, input_fd=0, output_fd=1):
    while True:
        ready, _, _ = select.select([input_fd], [], [], 0)
        if ready:
            data = os.read(input_fd, 16384)
            if not data:
                return
            client.send(data)
        data = client.receive()
        offset = 0
        while offset < len(data):
            written = os.write(output_fd, data[offset:])
            if written <= 0:
                raise EOFError('Owner stopped reading Carkit data')
            offset += written


def main():
    parser = argparse.ArgumentParser(description='Explicit Linux USB CarPlay preparation/bridge')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='Read-only sysfs discovery (no trust dialog)')
    setup = commands.add_parser('prepare', help='Find the selected iPhone NCM link')
    setup.add_argument('--udid', default='')
    setup.add_argument('--interface', default='')
    setup.add_argument('--timeout', type=int, default=30)
    setup.add_argument('--configure', action='store_true', help='Allow selected-iPhone mode/configuration changes')
    setup.add_argument('--bring-up', action='store_true', help='Allow setting only the selected NCM link UP')
    stream = commands.add_parser('connect', help=argparse.SUPPRESS)
    stream.add_argument('udid')
    args = parser.parse_args()
    client = None
    try:
        if args.command == 'list':
            print(json.dumps({'devices': [dict(udid=p.serial, bus=p.bus, address=p.address) for p in phones()]}))
        elif args.command == 'prepare':
            if not 1 <= args.timeout <= 120:
                raise ValueError('timeout must be in 1..120 seconds')
            print(json.dumps(prepare(args.udid, args.interface, args.configure, args.bring_up, args.timeout)))
        else:
            client = Carkit(args.udid)
            print('DIPLAY_CARKIT_READY', file=sys.stderr, flush=True)
            relay(client)
    except (Exception, KeyboardInterrupt) as error:
        print('USB: ' + str(error), file=sys.stderr, flush=True)
        return 2
    finally:
        if client:
            client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
