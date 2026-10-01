import argparse
import json
import logging
import signal
import sys
from pathlib import Path
from .config import Config, ROOT
from .protocol import EventQueue


def doctor(config):
    import platform
    import shutil
    import subprocess
    from .media import decoder_available
    report = {'platform': platform.machine(), 'python': platform.python_version(),
              'core_built': (ROOT / 'build/diplay-core.jar').is_file(),
              'programs': {x: bool(shutil.which(x)) for x in ['java', 'ip', 'iw', 'bluetoothctl', 'nmcli', 'ffmpeg']},
              'gstreamer': {x: decoder_available(x) for x in ['gtksink', 'appsrc', 'appsink', 'h264parse',
                  'vah264dec', 'vaapih264dec', 'v4l2h264dec', 'v4l2slh264dec', 'avdec_h264',
                  'rtpjitterbuffer', 'rtpL16depay', 'rtpmp4gdepay', 'avdec_aac', 'rtpopusdepay', 'opusenc']},
              'transport': config.transport,
              'note': 'Factory presence is not proof of hardware decoding or a working iPhone connection.'}
    if config.transport == 'wired':
        from ctypes.util import find_library
        from .usb import phones, ncm_interfaces
        report['usb'] = {'libimobiledevice': bool(find_library('imobiledevice-1.0')),
                         'idevice_id': bool(shutil.which('idevice_id')),
                         'phones': [{'ncm': ncm_interfaces(phone)} for phone in phones()],
                         'note': 'Read-only; no configuration change or trust dialog'}
    if config.transport == 'wireless' and shutil.which('iw'):
        result = subprocess.run(['iw', 'list'], capture_output=True, text=True, timeout=5)
        report['wifi_ap_reported'] = '* AP' in result.stdout
    print(json.dumps(report, indent=2))


class Application:
    def __init__(self, config, args):
        from gi.repository import GLib
        # Import GTK overrides before gtksink can create/cache its GtkWidget type.
        # Doing this after Media() breaks PyGObject's translate_coordinates override.
        if config.mode == 'native':
            from .native import NativeFrontend
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise RuntimeError('Native mode requires a working X11 or Wayland display')
        from .media import Media
        self.GLib = GLib
        self.config, self.args = config, args
        self.loop = GLib.MainLoop()
        self.queue = EventQueue()
        self.core = self.radio = self.usb = self.frontend = self.web = None
        self.generation = 0
        self.media = None
        self.exit_code = 0
        self.stopping = False
        self.connected_once = False
        self.stats = {'events': 0, 'errors': 0, 'mode': config.mode, 'demo': args.demo, 'transport': config.transport}
        config.prepare_private_state()
        self.drain_source = GLib.timeout_add(5, self.drain)
        if config.mode == 'web':
            from .web import WebFrontend
            self.web = WebFrontend(config, self.control, self.web_microphone,
                                   lambda: GLib.idle_add(self.web_connected), self.emit)
            self.web.start()
        self.media = Media(config, self.control, self.emit, self.web.push if self.web else None)
        if config.mode == 'native':
            self.frontend = NativeFrontend(self.media, self.control, self.stop)
        if args.demo:
            from .demo import Demo
            self.core = Demo(config, self.emit)
            self.core.start()
        else:
            self.start_connection()
        if args.duration:
            GLib.timeout_add(int(args.duration * 1000), self.stop)
        for signum in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self.stop)

    def start_connection(self):
        """Frontend and phone transport are independent choices, sharing one protocol core."""
        self.generation += 1
        generation = self.generation
        emit = lambda event: self.emit(dict(event, _generation=generation))
        control = lambda command: self.control(command) if generation == self.generation else None
        if self.config.transport == 'wired':
            from .wired import WiredTransport
            self.usb = WiredTransport(self.config.usb_settings(), emit, control)
            self.usb.prepare()
        else:
            from .radio import Radio
            from .core import Core
            net = self.config.network_settings()
            self.radio = Radio(net, emit, control)
            self.core = Core(self.config.core_settings(self.radio.bt_mac), emit)

    def usb_prepared(self, facts):
        from .core import Core
        generation = self.generation
        settings = self.config.core_settings(facts['device_mac'], wired=facts)
        self.core = Core(settings, lambda event: self.emit(dict(event, _generation=generation)))

    def emit(self, event):
        try:
            self.queue.push(event)
        except BufferError:
            logging.error('Broker queue overflow')
            self.exit_code = 2
            self.GLib.idle_add(self.stop)

    def control(self, value):
        if value.get('op') == 'reconnect':
            self.GLib.idle_add(self.reconnect)
        elif self.core:
            self.core.send(value)

    def reconnect(self):
        if self.stopping:
            return False
        if self.args.demo:
            self.core.send(dict(op='keyframe'))
        else:
            # Invalidate old queued callbacks before terminating transports/JVM. The GUI
            # and browser stay alive, while no stale USB EOF can kill the new session.
            self.generation += 1
            for obj in (self.radio, self.usb, self.core):
                if obj:
                    obj.close()
            self.radio = self.usb = self.core = None
            self.media.reset_session()
            try:
                self.start_connection()
            except Exception as error:
                self.emit(dict(event='error', component='connection', message=str(error)))
                self.emit(dict(event='status', state='reconnect_required'))
        return False

    def web_microphone(self, key, data):
        def apply():
            if self.media:
                if key == -1:
                    for ident in list(self.media.mics):
                        self.media._mic_stop(ident)
                else:
                    self.media.web_microphone(key, data)
            return False
        self.GLib.idle_add(apply)

    def web_connected(self):
        if self.media:
            self.media.web_connected()
        return False

    def drain(self):
        events, resync = self.queue.drain()
        if resync and self.media:
            self.media.resync()
        for event in events:
            if self.stopping:
                break
            event = dict(event)
            if event.pop('_generation', self.generation) != self.generation:
                continue
            self.stats['events'] += 1
            kind = event.get('event')
            try:
                if kind == 'usb_prepared' and self.usb:
                    self.usb_prepared(event['facts'])
                elif kind == 'ready' and self.usb:
                    self.usb.start()
                elif kind == 'usb_send' and self.usb:
                    self.usb.write(event['data'])
                elif kind == 'usb_release' and self.usb:
                    self.usb.close()
                elif kind == 'ready' and self.radio:
                    self.radio.start(event)
                elif kind == 'bt_send' and self.radio:
                    self.radio.write(event['data'])
                elif kind == 'bt_release' and self.radio:
                    self.radio.release_socket(handoff=True)
                elif kind in ('video_config', 'video', 'video_stop', 'audio_start', 'audio', 'audio_stop', 'mic_start', 'mic_stop'):
                    self.media.handle(event)
                else:
                    if kind in ('error', 'fatal'):
                        self.stats['errors'] += 1
                        logging.error('%s: %s', event.get('component', kind), event.get('message', ''))
                    elif kind in ('status', 'decoder', 'diagnostic'):
                        if kind == 'status':
                            event.setdefault('transport', self.config.transport)
                        logging.info('%s', json.dumps(event, ensure_ascii=False))
                    if kind == 'status' and event.get('state') == 'connected':
                        self.connected_once = True
                    if self.frontend:
                        self.frontend.status(event)
                    if self.web:
                        self.web.push(event)
                    if kind == 'fatal':
                        self.exit_code = 2; self.GLib.timeout_add(100, self.stop)
            except Exception as error:
                self.stats['errors'] += 1
                logging.exception('Component failed: %s', kind)
                self.emit(dict(event='fatal', message=f'{kind}: {type(error).__name__}'))
        return not self.stopping

    def stop(self):
        if self.stopping:
            return False
        self.stopping = True
        self.loop.quit()
        return False

    def close(self):
        self.stopping = True
        for obj in (self.radio, self.usb, self.core, self.media, self.web):
            if obj:
                try:
                    obj.close()
                except Exception:
                    logging.exception('Shutdown error')
        if self.media:
            self.stats.update(video_frames_received=self.media.frames,
                              video_frames_rendered=self.media.video_frames_rendered,
                              audio_rtp_packets=self.media.audio_packets,
                              decoder=self.media.selected_decoder,
                              physical_session_observed=self.connected_once)
        if self.args.stats:
            Path(self.args.stats).write_text(json.dumps(self.stats, indent=2))
        logging.info('Stopped: %s', json.dumps(self.stats))

    def run(self):
        try:
            self.loop.run()
        finally:
            self.close()
        return self.exit_code


def main():
    parser = argparse.ArgumentParser(description='DiPlay Linux wired/wireless receiver preview')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--mode', choices=['native', 'web'], default='native')
    parser.add_argument('--transport', choices=['wired', 'wireless'], help='Override connection.transport')
    parser.add_argument('--demo', action='store_true', help='Synthetic test pattern, no phone/authentication')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--duration', type=float, default=0, help='Stop after N seconds (smoke testing)')
    parser.add_argument('--stats', type=Path, help='Write nonsensitive test metrics on exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = Config.read(args.config, args.mode) if args.config else Config({}, args.mode)
    if args.transport:
        config.document['connection']['transport'] = args.transport
    if args.doctor:
        doctor(config); return 0
    if args.duration < 0 or args.duration > 86400:
        parser.error('duration must be in 0..86400 seconds')
    if not args.demo and not args.config:
        parser.error('Live mode needs --config with authentication and wired USB or wireless AP settings')
    application = None
    try:
        application = Application.__new__(Application)
        application.__init__(config, args)
        return application.run()
    except Exception:
        logging.exception('Startup failed')
        if application and hasattr(application, 'stats'):
            application.stats['errors'] += 1
            application.close()
        return 2


if __name__ == '__main__':
    sys.exit(main())
