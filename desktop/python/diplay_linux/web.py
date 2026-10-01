"""Single-controller local Web UI with explicit token and same-origin checks."""
import asyncio
import json
import logging
import secrets
import ssl
import struct
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from .config import ROOT
from .protocol import validate_control

ASSETS = {'/': 'index.html', '/client.js': 'client.js', '/input.js': 'input.js', '/audio-worklet.js': 'audio-worklet.js', '/style.css': 'style.css'}

def envelope(event):
    header = {k: v for k, v in event.items() if k != 'data'}
    meta = json.dumps(header, separators=(',', ':')).encode()
    return struct.pack('!I', len(meta)) + meta + event['data']

class WebFrontend:
    def __init__(self, config, control, microphone, connected, notify):
        self.config, self.control, self.microphone = config, control, microphone
        self.connected, self.notify = connected, notify
        self.loop = self.ws = self.queue = self.runner = None
        self.stopped = False
        self.wait_key = True
        self.latest_config = None
        self.latest_status = dict(event='status', state='starting')
        self.byte_count = self.scheduled = 0
        self.dropped_media = False
        self.schedule_lock = threading.Lock()
        self.ready = threading.Event()
        self.failure = None
        self.config.prepare_private_state()
        path = config.state_dir / 'web-token'
        if path.exists():
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError('web-token must be an owner-only regular file')
            self.token = path.read_text().strip()
            if len(self.token) < 32:
                raise ValueError('Invalid existing web-token')
        else:
            import os
            self.token = secrets.token_urlsafe(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(self.token + '\n')
        settings = config.document['web']
        host = settings['host']
        host = f'[{host}]' if ':' in host else host
        scheme = 'https' if settings.get('tls_certificate') else 'http'
        self.origin = settings.get('allowed_origin') or f"{scheme}://{host}:{settings['port']}"
        self.authority = urlsplit(self.origin).netloc

    def _check_host(self, request):
        if request.headers.get('Host') != self.authority:
            raise web.HTTPForbidden(text='Unrecognized Host')

    async def asset(self, request):
        self._check_host(request)
        name = ASSETS.get(request.path)
        if name is None:
            raise web.HTTPNotFound()
        result = web.FileResponse(ROOT / 'web' / name)
        result.headers.update({
            'Content-Security-Policy': "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; worker-src 'self'; object-src 'none'; frame-ancestors 'none'",
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
        })
        return result

    async def health(self, request):
        self._check_host(request)
        return web.json_response({'service': 'diplay-linux', 'mode': 'web', 'authentication': 'required'})

    async def websocket(self, request):
        self._check_host(request)
        if request.headers.get('Origin') != self.origin:
            raise web.HTTPForbidden(text='Origin mismatch')
        ws = web.WebSocketResponse(max_msg_size=16384, heartbeat=15, receive_timeout=40)
        await ws.prepare(request)
        try:
            first = await ws.receive(timeout=5)
            if first.type != WSMsgType.TEXT:
                raise ValueError('Authenticate first')
            credentials = json.loads(first.data)
            if not isinstance(credentials, dict):
                raise ValueError('Invalid authentication object')
            token = credentials.get('token', '')
            if credentials.get('op') != 'auth' or not isinstance(token, str) or not secrets.compare_digest(token, self.token):
                raise ValueError('Authentication failed')
        except (ValueError, TypeError, asyncio.TimeoutError):
            await ws.close(code=1008, message=b'Authentication required')
            return ws
        if self.ws is not None:
            await ws.close(code=1013, message=b'Another controller is connected')
            return ws
        self.ws = ws
        self.queue = asyncio.Queue(64)
        self.byte_count = 0
        self.wait_key = True
        await ws.send_json(dict(event='authorized'))
        await ws.send_json(self.latest_status)
        if self.latest_config:
            await ws.send_json(self.latest_config)
        self.connected()
        sender = asyncio.create_task(self._sender(ws))
        window, count = time.monotonic(), 0
        try:
            async for message in ws:
                now = time.monotonic()
                if now - window >= 1:
                    window, count = now, 0
                count += 1
                if count > 220:
                    await ws.close(code=1008, message=b'Input rate exceeded'); break
                if message.type == WSMsgType.TEXT:
                    value = json.loads(message.data)
                    if not isinstance(value, dict):
                        raise ValueError('Control must be an object')
                    if value.get('op') == 'reconnect':
                        self.control(dict(op='reconnect'))
                    else:
                        self.control(validate_control(value))
                elif message.type == WSMsgType.BINARY:
                    if not 4 < len(message.data) <= 8196 or len(message.data) % 2:
                        raise ValueError('Invalid microphone packet')
                    key, = struct.unpack('!I', message.data[:4])
                    self.microphone(key, message.data[4:])
                elif message.type == WSMsgType.ERROR:
                    break
        except (ValueError, KeyError, TypeError):
            await ws.close(code=1008, message=b'Invalid control')
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            self.ws = self.queue = None
            self.byte_count = 0
            self.control(dict(op='touch', contacts=[dict(x=0.0, y=0.0, down=False) for _ in range(2)]))
            self.microphone(-1, b'')
        return ws

    async def _sender(self, ws):
        try:
            while True:
                item = await self.queue.get()
                self.byte_count -= len(item) if isinstance(item, bytes) else len(item.encode())
                if isinstance(item, bytes):
                    await asyncio.wait_for(ws.send_bytes(item), 2)
                else:
                    await asyncio.wait_for(ws.send_str(item), 2)
        except (asyncio.TimeoutError, ConnectionError):
            await ws.close(code=1013, message=b'Client too slow')

    def push(self, event):
        if self.stopped or self.loop is None:
            return
        with self.schedule_lock:
            if self.scheduled >= 128:
                if event.get('event') in ('video', 'pcm'):
                    self.dropped_media = True
                    return
                self.notify(dict(event='error', component='web', message='Web event loop backlog'))
                return
            self.scheduled += 1
        self.loop.call_soon_threadsafe(self._enqueue_scheduled, event)

    def _enqueue_scheduled(self, event):
        with self.schedule_lock:
            self.scheduled -= 1
            dropped = self.dropped_media
            self.dropped_media = False
        if dropped:
            self.wait_key = True
            self.control(dict(op='keyframe'))
            self._enqueue(dict(event='resync'))
        self._enqueue(event)

    def _enqueue(self, event):
        kind = event.get('event')
        if kind == 'video_config':
            self.latest_config = event
            self.wait_key = True
        if kind == 'status':
            self.latest_status = event
        if kind == 'video_stop':
            self.latest_config = None
            self.wait_key = True
        if self.queue is None:
            return
        if kind == 'video':
            if event.get('key'):
                self.wait_key = False
            elif self.wait_key:
                return
        if kind == 'resync':
            self.wait_key = True
        encoded = envelope(event) if 'data' in event else json.dumps(event, separators=(',', ':'))
        size = len(encoded) if isinstance(encoded, bytes) else len(encoded.encode())
        if self.queue.full() or self.byte_count + size > 4 * 1024 * 1024:
            self.wait_key = True
            if self.ws:
                asyncio.create_task(self.ws.close(code=1013, message=b'Media queue overflow; reconnect'))
            return
        self.byte_count += size
        self.queue.put_nowait(encoded)

    async def _start(self):
        app = web.Application(client_max_size=16384)
        for path in ASSETS:
            app.router.add_get(path, self.asset)
        app.router.add_get('/health', self.health)
        app.router.add_get('/ws', self.websocket)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        options = self.config.document['web']
        context = None
        if options.get('tls_certificate'):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(options['tls_certificate'], options['tls_key'])
        await web.TCPSite(self.runner, options['host'], options['port'], ssl_context=context).start()

    def start(self):
        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._start())
                self.ready.set()
                self.loop.run_forever()
            except Exception as error:
                self.failure = error
                self.ready.set()
            finally:
                if self.ws is not None:
                    self.loop.run_until_complete(self.ws.close(code=1001, message=b'Receiver stopping'))
                if self.runner:
                    self.loop.run_until_complete(self.runner.cleanup())
                self.loop.close()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError('Web server startup timed out')
        if self.failure:
            raise self.failure
        logging.info('Web UI: %s ; token file: %s', self.origin, self.config.state_dir / 'web-token')

    def close(self):
        self.stopped = True
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=4)
