import io
import struct
import pytest
from diplay_linux.protocol import read_frame, encode_frame, avc_config, validate_control, EventQueue
from diplay_linux.config import Config


def test_wire_roundtrip():
    value = {'op': 'touch', 'contacts': [{'x': 0.25, 'y': 0.5, 'down': True}], 'data': bytes(range(256))}
    assert read_frame(io.BytesIO(encode_frame(value))) == value

@pytest.mark.parametrize('wire', [b'\0', b'\0\0\0', struct.pack('!I', 8), b'\0\0\0\x08x'])
def test_truncated(wire):
    with pytest.raises(EOFError): read_frame(io.BytesIO(wire))

@pytest.mark.parametrize('size', [0, 4*1024*1024+1, 0xffffffff])
def test_bad_size(size):
    with pytest.raises(ValueError): read_frame(io.BytesIO(struct.pack('!I', size)))


def test_codec_config():
    avcc = bytes.fromhex('0142C01EFF E100046742C01E01000268CE'.replace(' ', ''))
    codec, prefix = avc_config(avcc)
    assert codec == '42C01E'
    assert prefix == b'\0\0\0\1\x67\x42\xc0\x1e\0\0\0\1\x68\xce'
    for index in range(len(avcc)):
        with pytest.raises(ValueError): avc_config(avcc[:index])

@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, 2, '0.5', True])
def test_controls_reject_invalid_coordinates(value):
    with pytest.raises(ValueError):
        validate_control(dict(op='touch', contacts=[dict(x=value, y=0, down=True)]))


def test_controls_allowlist():
    assert validate_control(dict(op='key', key='home', ignored='data')) == dict(op='key', key='home')
    with pytest.raises(ValueError): validate_control(dict(op='bt_data', data='no'))
    with pytest.raises(ValueError): validate_control(dict(op='touch', contacts=[]))


def test_bounded_queue_preserves_control_and_signals_resync():
    q = EventQueue(limit=3)
    q.push(dict(event='status', state='demo'))
    q.push(dict(event='video', data=b'x')); q.push(dict(event='audio', data=b'x'))
    q.push(dict(event='video', data=b'y'))
    events, resync = q.drain()
    assert resync and events == [dict(event='status', state='demo'), dict(event='video', data=b'y')]
    assert q.drain() == ([], False)


def test_control_overflow_is_explicit():
    q = EventQueue(limit=2)
    for _ in range(2): q.push(dict(event='status'))
    with pytest.raises(BufferError): q.push(dict(event='status'))


def test_remote_web_requires_tls():
    with pytest.raises(ValueError): Config({'web': {'host': '0.0.0.0'}})
    assert Config({'web': {'host': '127.0.0.1'}})


def test_invalid_decoder_pipeline_rejected():
    with pytest.raises(ValueError): Config({'video': {'decoder': 'avdec_h264 ! filesink location=/tmp/x'}})


def test_credential_permissions(tmp_path):
    cfg = Config({'auth': {'mode': 'local', 'directory': str(tmp_path)},
                  'network': {'interface': 'lo', 'phone': '01:02:03:04:05:06', 'ssid': 'test',
                              'password': '12345678', 'address': '127.0.0.1'}})
    (tmp_path / 'identity.pk8').write_bytes(b'not-a-key'); (tmp_path / 'identity.pk8').chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): cfg.core_settings('11:22:33:44:55:66')
