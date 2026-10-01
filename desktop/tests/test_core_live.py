"""Actual JVM listener with a synthetic identity; NOT an iPhone handshake test."""
import queue
import socket
import subprocess
import threading
from pathlib import Path
import pytest
from diplay_linux.protocol import encode_frame, read_frame
from test_identity import synthetic_apk
from diplay_linux.identity import install_identity
ROOT=Path(__file__).resolve().parents[1]

def test_jvm_local_identity_and_rtsp_listener(tmp_path):
    jar=ROOT/'build/diplay-core.jar'
    if not jar.is_file():pytest.skip('Compile JVM core first')
    identity=tmp_path/'identity';install_identity(synthetic_apk(tmp_path/'synthetic.apk'),identity)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    settings=dict(state_dir=str(tmp_path/'state'),width=640,height=360,fps=30,
                  bt_mac='02:00:00:00:00:01',address='127.0.0.1',name='Synthetic test',
                  airplay_port=port,microphone=False,auth_mode='local',auth_dir=str(identity),
                  ssid='synthetic-test',password='synthetic-test',channel=36)
    process=subprocess.Popen(['java','-Xmx128m','-cp',str(jar)+':'+str(ROOT/'build/bcprov.jar'),
                              'dev.diplay.desktop.MainKt'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    events=queue.Queue()
    def receive():
        try:
            while (event:=read_frame(process.stdout)) is not None:events.put(event)
        except Exception as e:events.put(e)
    threading.Thread(target=receive,daemon=True).start()
    try:
        process.stdin.write(encode_frame(dict(op='start',settings=settings)));process.stdin.flush()
        first=events.get(timeout=10)
        assert isinstance(first,dict) and first['event']=='ready',first
        state=events.get(timeout=5)
        assert state['state']=='waiting_for_phone',state
        with socket.create_connection(('127.0.0.1',port),timeout=5) as conn:
            conn.sendall(b'GET /info RTSP/1.0\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n')
            response=conn.recv(8192)
            assert b'200' in response.split(b'\r\n',1)[0],response[:100]
        process.stdin.write(encode_frame(dict(op='stop')));process.stdin.flush()
        assert process.wait(timeout=5)==0
    finally:
        if process.poll() is None:process.kill();process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream: stream.close()


def test_jvm_wired_listener_and_usb_stream_without_radio_configuration(tmp_path):
    """Real JVM and iAP2 engine, synthetic peer/identity. No USB hardware claim."""
    jar = ROOT/'build/diplay-core.jar'
    if not jar.is_file():
        pytest.skip('Compile JVM core first')
    identity = tmp_path/'identity'
    install_identity(synthetic_apk(tmp_path/'synthetic.apk'), identity)
    try:
        with socket.socket(socket.AF_INET6) as sock:
            sock.bind(('::1', 0))
            port = sock.getsockname()[1]
    except OSError:
        pytest.skip('IPv6 loopback unavailable in test runner')
    settings = dict(transport='wired', state_dir=str(tmp_path/'state'), width=640, height=360, fps=30,
                    bt_mac='02:00:00:00:00:01', address='::1', name='Synthetic USB test',
                    airplay_port=port, microphone=False, auth_mode='local', auth_dir=str(identity),
                    usb_interface_number=3)
    # Deliberately NO Wi-Fi SSID/password/channel and NO Bluetooth setup.
    process = subprocess.Popen(['java', '-Xmx128m', '-cp', str(jar)+':'+str(ROOT/'build/bcprov.jar'),
                                'dev.diplay.desktop.MainKt'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    events = queue.Queue()
    def receive():
        try:
            while (event := read_frame(process.stdout)) is not None:
                events.put(event)
        except Exception as error:
            events.put(error)
    threading.Thread(target=receive, daemon=True).start()
    def send(value):
        process.stdin.write(encode_frame(value)); process.stdin.flush()
    try:
        send(dict(op='start', settings=settings))
        first = events.get(timeout=10)
        assert first['event'] == 'ready' and first['transport'] == 'wired', first
        assert events.get(timeout=5)['state'] == 'waiting_for_phone'
        with socket.create_connection(('::1', port), timeout=5) as conn:
            conn.sendall(b'GET /info RTSP/1.0\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n')
            assert b'200' in conn.recv(8192).split(b'\r\n', 1)[0]
        send(dict(op='usb_open'))
        import time
        deadline = time.monotonic() + 8
        saw_usb = False
        while time.monotonic() < deadline:
            event = events.get(timeout=3)
            assert event.get('event') != 'bt_send', event
            if event.get('event') == 'usb_send':
                assert event['data']
                saw_usb = True
                break
        assert saw_usb, 'Wired iAP2 engine did not write to the USB pipe'
        send(dict(op='usb_closed'))
        send(dict(op='stop'))
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream: stream.close()
