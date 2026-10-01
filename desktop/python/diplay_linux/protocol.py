"""Bounded IPC and media helpers, independent of GTK/BlueZ for unit testing."""
import collections
import math
import plistlib
import struct
import threading

MAX_FRAME = 4 * 1024 * 1024


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        block = stream.read(size - len(result))
        if not block:
            if not result:
                return None
            raise EOFError('Truncated IPC frame')
        result.extend(block)
    return bytes(result)


def read_frame(stream):
    header = read_exact(stream, 4)
    if header is None:
        return None
    size, = struct.unpack('!I', header)
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Invalid IPC frame size')
    data = read_exact(stream, size)
    if data is None:
        raise EOFError('Missing IPC payload')
    value = plistlib.loads(data)
    if not isinstance(value, dict):
        raise ValueError('IPC object must be a dictionary')
    return value


def encode_frame(value):
    data = plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=False)
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError('IPC frame too large')
    return struct.pack('!I', len(data)) + data


def avc_config(data):
    if len(data) < 7 or data[0] != 1:
        raise ValueError('Invalid avcC')
    position = 6
    chunks = []
    for count in (data[5] & 31,):
        for _ in range(count):
            if position + 2 > len(data):
                raise ValueError('Truncated SPS')
            size, = struct.unpack_from('!H', data, position)
            position += 2
            if size == 0 or position + size > len(data):
                raise ValueError('Invalid SPS length')
            chunks.append(data[position:position + size]); position += size
    if position >= len(data):
        raise ValueError('Missing PPS')
    count = data[position]; position += 1
    for _ in range(count):
        if position + 2 > len(data):
            raise ValueError('Truncated PPS')
        size, = struct.unpack_from('!H', data, position); position += 2
        if size == 0 or position + size > len(data):
            raise ValueError('Invalid PPS length')
        chunks.append(data[position:position + size]); position += size
    if not chunks or not count:
        raise ValueError('Missing parameter sets')
    return ''.join(f'{x:02X}' for x in data[1:4]), b''.join(b'\0\0\0\1' + c for c in chunks)


def validate_control(value):
    if not isinstance(value, dict):
        raise ValueError('Control must be an object')
    op = value.get('op')
    if op == 'touch':
        contacts = value.get('contacts')
        if not isinstance(contacts, list) or not 1 <= len(contacts) <= 2:
            raise ValueError('One or two contacts required')
        clean = []
        for c in contacts:
            if not isinstance(c, dict) or type(c.get('down')) is not bool:
                raise ValueError('Invalid contact')
            x, y = c.get('x'), c.get('y')
            if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in (x, y)):
                raise ValueError('Coordinates must be finite and normalized')
            clean.append(dict(x=float(x), y=float(y), down=c['down']))
        return dict(op=op, contacts=clean)
    if op == 'key' and value.get('key') in ('home', 'back', 'select', 'siri', 'left', 'right', 'up', 'down'):
        return dict(op=op, key=value['key'])
    if op in ('keyframe', 'rendered', 'disconnect'):
        return dict(op=op)
    raise ValueError('Unsupported control')


class EventQueue:
    """Never block a protocol receive thread on media/UI. Overflow forces resync."""
    def __init__(self, limit=128, byte_limit=8 * 1024 * 1024):
        self.limit, self.byte_limit = limit, byte_limit
        self.queue = collections.deque()
        self.bytes = 0
        self.lock = threading.Lock()
        self.resync = False

    def push(self, event):
        size = len(event.get('data', b'')) + 256
        with self.lock:
            if len(self.queue) >= self.limit or self.bytes + size > self.byte_limit:
                # Preserve control-plane events. Dropped interframes must not be decoded.
                kept = [e for e in self.queue if e.get('event') not in ('video', 'audio')]
                self.queue = collections.deque(kept)
                self.bytes = sum(len(e.get('data', b'')) + 256 for e in kept)
                self.resync = True
            if len(self.queue) >= self.limit or self.bytes + size > self.byte_limit:
                raise BufferError('Control queue overflow')
            self.queue.append(event); self.bytes += size

    def drain(self):
        with self.lock:
            events = list(self.queue); self.queue.clear(); self.bytes = 0
            resync = self.resync; self.resync = False
        return events, resync
