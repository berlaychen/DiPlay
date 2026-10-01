"""GStreamer adapters; native GTK has no browser and Web forwards compressed AVC.

Pipelines are bounded and report failures. This preview is not zero-copy.
"""
import time
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from .protocol import avc_config
from .preferences import role_group

Gst.init(None)
RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]


def make_buffer(data):
    buf = Gst.Buffer.new_allocate(None, len(data), None)
    buf.fill(0, data)
    return buf


def decoder_available(name):
    return Gst.ElementFactory.find(name) is not None


def decoder_choice(requested):
    options = [requested] if requested != 'auto' else [
        'vah264dec', 'vaapih264dec', 'v4l2h264dec', 'v4l2slh264dec', 'avdec_h264']
    for name in options:
        if decoder_available(name):
            return name
    raise RuntimeError('No H.264 decoder available; install GStreamer libav or the appropriate hardware driver')


class Media:
    def __init__(self, config, command, notify, web_send=None):
        self.config, self.command, self.notify = config, command, notify
        self.web_send = web_send
        self.mode = config.mode
        self.video = self.video_src = self.video_widget = None
        self.sps_pps = b''
        self.video_info = None
        self.wait_key = True
        self.last_key_request = 0
        self.origin = None
        self.audio = {}
        self.mics = {}
        self.closed = False
        self.frames = self.video_frames_rendered = self.audio_packets = 0
        self.selected_decoder = ''
        self.buses = {}
        self.audio_test = config.document.get('test_audio_sink', False)
        if self.mode == 'native':
            self._create_video(config.document['video']['decoder'])

    def _watch(self, pipeline, component):
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        handle = bus.connect('message', self._bus_message, component)
        self.buses[pipeline] = (bus, handle)

    def _dispose(self, pipeline):
        pipeline.set_state(Gst.State.NULL)
        watch = self.buses.pop(pipeline, None)
        if watch:
            watch[0].disconnect(watch[1])
            watch[0].remove_signal_watch()

    def _bus_message(self, bus, message, component):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.notify(dict(event='error', component=component, message=error.message[:400]))
            if component == 'video':
                self.wait_key = True
                if self.config.document['video']['decoder'] == 'auto' and self.selected_decoder != 'avdec_h264':
                    GLib.idle_add(self._fallback_video)

    def _fallback_video(self):
        if self.closed or self.selected_decoder == 'avdec_h264':
            return False
        sink = self.video.get_by_name('display')
        self._dispose(self.video)
        self.video.remove(sink)
        self._create_video('avdec_h264', sink)
        self.wait_key = True
        self._request_key()
        return False

    def _create_video(self, requested, existing_sink=None):
        name = decoder_choice(requested)
        self.selected_decoder = name
        description = ('appsrc name=video_in is-live=true format=time block=false max-bytes=2097152 '
                       'caps="video/x-h264,stream-format=byte-stream,alignment=au" '
                       f'! h264parse ! {name} ! videoconvert ! video/x-raw,format=BGRx '
                       '! identity name=rendered signal-handoffs=true ')
        if existing_sink is None:
            pipeline = Gst.parse_launch(description + '! gtksink name=display sync=false')
        else:
            pipeline = Gst.parse_launch(description)
            pipeline.add(existing_sink)
            if not pipeline.get_by_name('rendered').link(existing_sink):
                raise RuntimeError('Cannot relink GTK output')
        self.video, self.video_src = pipeline, pipeline.get_by_name('video_in')
        self.video_widget = pipeline.get_by_name('display').get_property('widget')
        pipeline.get_by_name('rendered').connect('handoff', self._rendered)
        self._watch(pipeline, 'video')
        # First startup waits for NativeFrontend to parent the widget. Otherwise
        # gtksink creates a separate window and touch no longer overlays the video.
        if existing_sink is not None:
            self.start_native()
        self.notify(dict(event='decoder', name=name, hardware=name != 'avdec_h264'))

    def start_native(self):
        if self.mode != 'native' or self.video_widget.get_parent() is None:
            raise RuntimeError('Parent the native video widget before starting playback')
        if self.video.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Cannot start native video pipeline')

    def _rendered(self, identity, buffer):
        self.video_frames_rendered += 1
        if self.video_frames_rendered == 1:
            self.command(dict(op='rendered'))

    def _request_key(self):
        if time.monotonic() - self.last_key_request > 1:
            self.last_key_request = time.monotonic()
            self.command(dict(op='keyframe'))

    def resync(self):
        self.wait_key = True
        self._request_key()
        if self.web_send:
            self.web_send(dict(event='resync'))

    def handle(self, event):
        kind = event['event']
        if kind == 'video_config':
            codec, self.sps_pps = avc_config(event['data'])
            self.video_info = dict(event='video_config', codec='avc1.' + codec,
                                   width=self.config.document['video']['width'],
                                   height=self.config.document['video']['height'])
            self.wait_key = True
            self.origin = None
            self.video_frames_rendered = 0
            if self.web_send:
                self.web_send(self.video_info)
        elif kind == 'video':
            if not self.sps_pps:
                return
            if event.get('key'):
                self.wait_key = False
                data = self.sps_pps + event['data']
            elif self.wait_key:
                self._request_key()
                return
            else:
                data = event['data']
            self.frames += 1
            if self.mode == 'web':
                self.web_send(dict(event='video', data=data, key=bool(event.get('key')), time_us=event['time_us']))
            else:
                buffer = make_buffer(data)
                if self.origin is None:
                    self.origin = event['time_us']
                buffer.pts = max(0, event['time_us'] - self.origin) * 1000
                buffer.dts = Gst.CLOCK_TIME_NONE
                if self.video_src.get_property('current-level-bytes') > 2 * 1024 * 1024:
                    self.resync()
                    return
                result = self.video_src.emit('push-buffer', buffer)
                if result not in (Gst.FlowReturn.OK, Gst.FlowReturn.FLUSHING):
                    self.notify(dict(event='error', component='video', message='Video pipeline rejected a buffer'))
        elif kind == 'video_stop':
            self.wait_key = True
            self.sps_pps = b''
            self.video_info = None
            if self.web_send:
                self.web_send(event)
        elif kind == 'audio_start':
            self._audio_start(event)
        elif kind == 'audio':
            stream = self.audio.get(event['id'])
            if stream:
                src = stream[1]
                if src.get_property('current-level-bytes') < 262144:
                    src.emit('push-buffer', make_buffer(event['data']))
                    self.audio_packets += 1
        elif kind == 'audio_stop':
            self._audio_stop(event['id'])
        elif kind == 'mic_start':
            self._mic_start(event)
        elif kind == 'mic_stop':
            self._mic_stop(event['id'])

    def _audio_start(self, event):
        key, rate, channels = int(event['id']), int(event['rate']), int(event['channels'])
        if not 8000 <= rate <= 96000 or channels not in (1, 2):
            raise ValueError('Unsupported audio format')
        self._audio_stop(key)
        if len(self.audio) >= 8:
            raise RuntimeError('Too many audio streams')
        codec = event['codec']
        caps = f'application/x-rtp,media=audio,clock-rate={rate},channels={channels}'
        if codec == 'LPCM':
            caps += ',encoding-name=L16'
            decode = 'rtpL16depay'
        elif codec == 'AAC_LC':
            if rate not in RATES:
                raise ValueError('Unsupported AAC sample rate')
            asc = ((2 << 11) | (RATES.index(rate) << 7) | (channels << 3)).to_bytes(2, 'big').hex()
            caps += (f',encoding-name=MPEG4-GENERIC,mode=AAC-hbr,streamtype=5,config=(string){asc},'
                     'sizelength=13,indexlength=3,indexdeltalength=3')
            decode = 'rtpmp4gdepay ! avdec_aac'
        elif codec == 'OPUS':
            caps = f'application/x-rtp,media=audio,clock-rate=48000,encoding-name=OPUS,channels={channels}'
            decode = 'rtpopusdepay ! opusdec'
        else:
            raise ValueError('Unsupported audio codec ' + codec)
        output = ('appsink name=pcm emit-signals=true sync=false max-buffers=8 drop=true' if self.mode == 'web'
                  else ('fakesink sync=false' if self.audio_test else 'autoaudiosink'))
        pipeline = Gst.parse_launch(
            'appsrc name=audio_in is-live=true format=time do-timestamp=true block=false max-bytes=262144 '
            f'caps="{caps}" ! rtpjitterbuffer latency=40 drop-on-latency=true ! {decode} '
            '! audioconvert ! audioresample ! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2 '
            '! volume name=level ! ' + output)
        volume = pipeline.get_by_name('level')
        self.audio[key] = (pipeline, pipeline.get_by_name('audio_in'), event.get('role', 'default'), volume)
        self._watch(pipeline, f'audio-{key}')
        if self.mode == 'web':
            pipeline.get_by_name('pcm').connect('new-sample', self._pcm_sample, key)
            self.web_send(dict(event='audio_start', id=key, rate=48000, channels=2, role=event.get('role', 'default')))
        self._duck()
        pipeline.set_state(Gst.State.PLAYING)

    def _pcm_sample(self, sink, key):
        sample = sink.emit('pull-sample')
        if sample and not self.closed:
            buffer = sample.get_buffer()
            self.web_send(dict(event='pcm', id=key, data=buffer.extract_dup(0, buffer.get_size())))
        return Gst.FlowReturn.OK

    def _duck(self):
        priority = any(s[2] in ('telephony', 'speechrecognition', 'alert', 'guidance') for s in self.audio.values())
        gains = self.config.document['audio'].get('volumes', {})
        for pipeline, source, role, volume in self.audio.values():
            duck = 0.3 if priority and role in ('default', 'media') else 1.0
            level = gains.get('master', 1.0) * gains.get(role_group(role), 1.0) * duck
            volume.set_property('volume', level)

    def _audio_stop(self, key):
        entry = self.audio.pop(key, None)
        if entry:
            self._dispose(entry[0])
            if self.web_send:
                self.web_send(dict(event='audio_stop', id=key))
            self._duck()

    def _mic_start(self, event):
        key = int(event['id'])
        self._mic_stop(key)
        if not self.config.document['audio']['microphone']:
            return
        rate, channels, size = int(event['rate']), int(event['channels']), int(event['frame_bytes'])
        if not 8000 <= rate <= 48000 or channels not in (1, 2) or not 0 < size <= 16384:
            raise ValueError('Unsupported microphone format')
        codec = event['codec']
        if codec not in ('LPCM', 'OPUS'):
            raise ValueError('Unsupported microphone codec')
        if self.mode == 'web':
            source = ('appsrc name=mic_in is-live=true format=time do-timestamp=true block=false max-bytes=32768 '
                      'caps="audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=1"')
        else:
            source = 'autoaudiosrc'
        encode = f'! opusenc bitrate={min(128000, max(16000, int(event.get("bitrate", 48000))))} frame-size=20 ' if codec == 'OPUS' else ''
        pipeline = Gst.parse_launch(source + ' ! audioconvert ! audioresample '
                                    f'! audio/x-raw,format=S16LE,layout=interleaved,rate={rate},channels={channels} '
                                    + encode + '! appsink name=mic_out emit-signals=true sync=false max-buffers=8 drop=true')
        state = dict(pipeline=pipeline, source=pipeline.get_by_name('mic_in'), pending=bytearray(),
                     codec=codec, size=size, key=key)
        pipeline.get_by_name('mic_out').connect('new-sample', self._mic_sample, state)
        self.mics[key] = state
        self._watch(pipeline, f'microphone-{key}')
        pipeline.set_state(Gst.State.PLAYING)
        if self.web_send:
            self.web_send(dict(event='mic_start', id=key))

    def _mic_sample(self, sink, state):
        sample = sink.emit('pull-sample')
        if not sample or self.closed:
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0, buffer.get_size())
        if state['codec'] == 'OPUS':
            self.command(dict(op='mic_packet', id=state['key'], data=data))
        else:
            pending = state['pending']
            pending.extend(data)
            while len(pending) >= state['size']:
                chunk = bytes(pending[:state['size']])
                del pending[:state['size']]
                self.command(dict(op='mic_packet', id=state['key'], data=chunk))
        return Gst.FlowReturn.OK

    def web_microphone(self, key, data):
        state = self.mics.get(key)
        if state and state['source'] and len(data) <= 8192 and len(data) % 2 == 0:
            src = state['source']
            if src.get_property('current-level-bytes') < 32768:
                src.emit('push-buffer', make_buffer(data))

    def _mic_stop(self, key):
        state = self.mics.pop(key, None)
        if state:
            self._dispose(state['pipeline'])
            if self.web_send:
                self.web_send(dict(event='mic_stop', id=key))

    def web_connected(self):
        if self.video_info:
            self.web_send(self.video_info)
        for key, state in self.audio.items():
            self.web_send(dict(event='audio_start', id=key, rate=48000, channels=2, role=state[2]))
        self.resync()

    def reset_session(self):
        for key in list(self.audio):
            self._audio_stop(key)
        for key in list(self.mics):
            self._mic_stop(key)
        self.sps_pps = b''
        self.video_info = None
        self.wait_key = True
        self.origin = None
        self.video_frames_rendered = 0
        if self.video:
            self.video.set_state(Gst.State.READY)
            self.start_native()
        if self.web_send:
            self.web_send(dict(event='video_stop'))

    def close(self):
        self.closed = True
        if self.video:
            self._dispose(self.video)
        for key in list(self.audio):
            self._audio_stop(key)
        for key in list(self.mics):
            self._mic_stop(key)
