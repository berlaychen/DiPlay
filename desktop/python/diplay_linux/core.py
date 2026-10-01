import logging
import queue
import subprocess
import threading
from .config import ROOT
from .protocol import encode_frame, read_frame


class Core:
    def __init__(self, settings, emit):
        self.emit = emit
        self.closed = threading.Event()
        self.outbound = queue.Queue(128)
        jar = ROOT / 'build/diplay-core.jar'
        if not jar.is_file():
            raise RuntimeError('Missing build/diplay-core.jar; build or unpack a release first')
        classpath = str(jar) + ':' + str(ROOT / 'build/bcprov.jar')
        self.process = subprocess.Popen(['java', '-Xms32m', '-Xmx256m', '-XX:+UseSerialGC',
                                        '-cp', classpath, 'dev.diplay.desktop.MainKt'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for target in (self._read, self._write, self._stderr):
            threading.Thread(target=target, daemon=True).start()
        self.send(dict(op='start', settings=settings))

    def send(self, message):
        if self.closed.is_set():
            return
        try:
            self.outbound.put_nowait(encode_frame(message))
        except queue.Full:
            self.emit(dict(event='fatal', message='Core input queue overflow'))
            self.close()

    def _read(self):
        try:
            while not self.closed.is_set():
                value = read_frame(self.process.stdout)
                if value is None:
                    break
                self.emit(value)
        except (ValueError, EOFError, OSError) as error:
            if not self.closed.is_set():
                self.emit(dict(event='fatal', message=f'Core IPC: {type(error).__name__}'))
        finally:
            if not self.closed.is_set():
                self.emit(dict(event='fatal', message='Core process exited'))

    def _write(self):
        try:
            while not self.closed.is_set():
                data = self.outbound.get()
                if data is None:
                    return
                self.process.stdin.write(data); self.process.stdin.flush()
        except (OSError, ValueError):
            if not self.closed.is_set():
                self.emit(dict(event='fatal', message='Core input closed'))

    def _stderr(self):
        # Core deliberately redacts upstream protocol/credential log strings.
        for line in self.process.stderr:
            logging.info('core: %s', line.decode(errors='replace').strip())

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.outbound.put_nowait(None)
        except queue.Full:
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=2)
