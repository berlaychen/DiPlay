"""Synthetic A/V fixture. Never reports authentication or an iPhone connection."""
import math
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import threading
import time


def split_nals(data):
    return [part for part in re.split(b'\x00\x00\x00?\x01', data) if part]


def read_fixture(data):
    nals = split_nals(data)
    sps = next(n for n in nals if n[0] & 31 == 7)
    pps = next(n for n in nals if n[0] & 31 == 8)
    config = bytes([1, sps[1], sps[2], sps[3], 0xff, 0xe1]) + struct.pack('!H', len(sps)) + sps
    config += b'\x01' + struct.pack('!H', len(pps)) + pps
    frames, current = [], []
    for nal in nals:
        if nal[0] & 31 == 9 and current:
            frames.append(current); current = []
        current.append(nal)
    if current:
        frames.append(current)
    return config, [(b''.join(b'\0\0\0\1' + n for n in frame), any(n[0] & 31 == 5 for n in frame))
                    for frame in frames if any(n[0] & 31 in (1, 5) for n in frame)]


class Demo:
    def __init__(self, config, emit):
        self.emit = emit
        self.stop = threading.Event()
        self.reset = threading.Event()
        self.config = config
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        v = self.config.document['video']
        try:
            with tempfile.TemporaryDirectory(prefix='diplay-demo-') as directory:
                path = Path(directory) / 'test.h264'
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                                '-i', f"testsrc2=size={v['width']}x{v['height']}:rate={v['fps']}",
                                '-t', '2', '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                                '-tune', 'zerolatency', '-profile:v', 'baseline', '-pix_fmt', 'yuv420p',
                                '-x264-params', f"aud=1:keyint={v['fps']}:scenecut=0", '-f', 'h264', str(path)],
                               check=True, timeout=30)
                avcc, frames = read_fixture(path.read_bytes())
            if not frames:
                raise ValueError('Empty demo fixture')
            self.emit(dict(event='status', state='DEMO - synthetic stream, no iPhone'))
            self.emit(dict(event='video_config', data=avcc))
            self.emit(dict(event='audio_start', id=1, codec='LPCM', rate=48000, channels=2, role='media'))
            start = time.monotonic(); next_video = next_audio = start
            sequence, samples, frame = 0, 0, 0
            while not self.stop.is_set():
                now = time.monotonic()
                if self.reset.is_set():
                    frame = 0; self.reset.clear()
                if now >= next_video:
                    data, key = frames[frame % len(frames)]
                    self.emit(dict(event='video', data=data, key=key, time_us=int(now * 1e6)))
                    frame += 1; next_video += 1 / v['fps']
                    if next_video < now - 0.2:
                        next_video = now
                if now >= next_audio:
                    pcm = bytearray()
                    for n in range(960):
                        value = int(1200 * math.sin(2 * math.pi * 440 * (samples + n) / 48000))
                        pcm.extend(struct.pack('!hh', value, value))
                    rtp = struct.pack('!BBHII', 0x80, 96, sequence & 65535, samples & 0xffffffff, 1) + pcm
                    self.emit(dict(event='audio', id=1, data=bytes(rtp)))
                    samples += 960; sequence += 1; next_audio += 0.020
                    if next_audio < now - 0.2:
                        next_audio = now
                self.stop.wait(max(0.001, min(next_video, next_audio) - time.monotonic()))
        except Exception as error:
            self.emit(dict(event='fatal', message=f'Demo failed: {error}'))

    def send(self, message):
        if message.get('op') in ('keyframe', 'reconnect'):
            self.reset.set()
        if message.get('op') == 'rendered':
            self.emit(dict(event='diagnostic', component='demo', message='Synthetic frame rendered'))

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
