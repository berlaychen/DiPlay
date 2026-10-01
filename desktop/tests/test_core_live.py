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
