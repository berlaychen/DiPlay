"""Bounded/cancellable process adapter shared by native and Web frontends."""
import json
import os
import queue
import subprocess
import sys
import threading
from .config import ROOT


class WiredTransport:
    def __init__(self, settings, emit, control):
        self.settings, self.emit, self.control = settings, emit, control
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.outgoing = queue.Queue(64)
        self.process = self.preparation = None
        self.facts = None
        self.timer = None
        self.lock = threading.Lock()
        self.failed = False

    def _command(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'python'))
        return [sys.executable, '-m', 'diplay_linux.usb_helper', *args], env

    def _spawn(self, *args, **options):
        command, env = self._command(*args)
        return subprocess.Popen(command, env=env, **options)

    def prepare(self):
        self.emit(dict(event='status', state='preparing_usb'))
        def run():
            args = ['prepare', '--timeout', str(self.settings['timeout'])]
            for key in ('udid', 'interface'):
                if self.settings.get(key):
                    args += ['--' + key, self.settings[key]]
            for key in ('configure', 'bring_up'):
                if self.settings[key]:
                    args.append('--' + key.replace('_', '-'))
            try:
                with self.lock:
                    if self.closed.is_set():
                        return
                    self.preparation = process = self._spawn(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    output, error = process.communicate(timeout=self.settings['timeout'] + 10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.communicate()
                    raise RuntimeError('USB preparation did not finish before its deadline')
                if self.closed.is_set():
                    return
                if process.returncode != 0:
                    raise RuntimeError(error.decode(errors='replace').strip()[:600] or 'USB preparation failed')
                if len(output) > 16384:
                    raise ValueError('USB helper returned oversized metadata')
                facts = json.loads(output)
                if not isinstance(facts, dict):
                    raise ValueError('USB helper returned invalid metadata')
                self.facts = facts
                self.emit(dict(event='usb_prepared', facts=facts))
            except Exception as error:
                self._fail(str(error))
        threading.Thread(target=run, name='diplay-usb-prepare', daemon=True).start()

    def start(self):
        """Called only after the shared AirPlay listener is ready."""
        if self.facts is None:
            raise RuntimeError('Prepare the NCM link before opening Carkit')
        with self.lock:
            if self.closed.is_set():
                return
            self.process = self._spawn('connect', self.facts['udid'], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.emit(dict(event='status', state='waiting_for_usb_trust'))
        self.timer = threading.Timer(self.settings['timeout'], self._trust_timeout)
        self.timer.daemon = True
        self.timer.start()
        for function in (self._read, self._write, self._status):
            threading.Thread(target=function, daemon=True).start()

    def _trust_timeout(self):
        if not self.ready.is_set():
            self._fail('USB trust/service timed out. Unlock iPhone, approve Trust and reconnect.')
            self._terminate(self.process)

    def _read(self):
        if not self.ready.wait(self.settings['timeout'] + 1) or self.closed.is_set():
            return
        try:
            while not self.closed.is_set():
                data = os.read(self.process.stdout.fileno(), 16384)
                if not data:
                    break
                self.control(dict(op='usb_data', data=data))
        except (OSError, ValueError):
            pass
        if not self.closed.is_set():
            self.control(dict(op='usb_closed'))
            self._fail('USB Carkit disconnected. Reconnect the cable/phone and select Reconnect.')

    def _write(self):
        try:
            while not self.closed.is_set():
                data = self.outgoing.get()
                if data is None:
                    return
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (OSError, ValueError):
            if not self.closed.is_set():
                self._fail('USB Carkit input closed')

    def _status(self):
        try:
            for raw in self.process.stderr:
                if self.closed.is_set():
                    return
                line = raw.decode(errors='replace').strip()
                if line == 'DIPLAY_CARKIT_READY':
                    self.timer.cancel()
                    # Queue open before making a byte readable by the protocol core.
                    self.control(dict(op='usb_open'))
                    self.ready.set()
                elif line.startswith('USB:'):
                    self._fail(line[:600])
        finally:
            if not self.ready.is_set() and not self.closed.is_set():
                self._fail('Carkit helper stopped before a trusted USB channel was opened')

    def write(self, data):
        if self.closed.is_set():
            return
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ValueError('Invalid USB control chunk')
        try:
            self.outgoing.put_nowait(data)
        except queue.Full:
            self._fail('USB control output overflow')
            self._terminate(self.process)

    def _fail(self, message):
        with self.lock:
            if self.closed.is_set() or self.failed:
                return
            self.failed = True
        self.emit(dict(event='error', component='usb', message=message))
        self.emit(dict(event='status', state='usb_error_reconnect_required'))

    @staticmethod
    def _terminate(process):
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self.closed.set()
            processes = (self.preparation, self.process)
        self.ready.set()
        if self.timer:
            self.timer.cancel()
        try:
            self.outgoing.put_nowait(None)
        except queue.Full:
            pass
        for process in processes:
            self._terminate(process)
