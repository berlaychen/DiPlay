import asyncio
import socket
import aiohttp
import pytest
from diplay_linux.config import Config
from diplay_linux.web import WebFrontend


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]


def test_web_host_origin_auth_and_control(tmp_path):
    port = free_port(); commands = []
    cfg = Config({'state_dir': str(tmp_path), 'web': {'port': port}}, 'web')
    server = WebFrontend(cfg, commands.append, lambda *a: None, lambda: None, lambda e: None)
    server.start()
    async def check():
        async with aiohttp.ClientSession() as session:
            async with session.get(server.origin + '/health') as response:
                assert response.status == 200
                assert (await response.json())['authentication'] == 'required'
            async with session.get(server.origin, headers={'Host': 'attacker.example'}) as response:
                assert response.status == 403
            async with session.get(server.origin + '/does-not-exist') as response: assert response.status == 404
            with pytest.raises(aiohttp.WSServerHandshakeError) as error:
                await session.ws_connect(server.origin + '/ws', origin='http://attacker.example')
            assert error.value.status == 403
            for bad_value in ({'op':'auth','token':'bad'}, [], None):
                bad = await session.ws_connect(server.origin + '/ws', origin=server.origin)
                await bad.send_json(bad_value)
                assert (await bad.receive()).type == aiohttp.WSMsgType.CLOSE
                await bad.close()
            ws = await session.ws_connect(server.origin + '/ws', origin=server.origin)
            await ws.send_json({'op': 'auth', 'token': server.token})
            assert (await ws.receive_json())['event'] == 'authorized'
            assert (await ws.receive_json())['event'] == 'status'
            other = await session.ws_connect(server.origin + '/ws', origin=server.origin)
            await other.send_json({'op':'auth', 'token':server.token})
            assert (await other.receive()).type == aiohttp.WSMsgType.CLOSE
            await other.close()
            await ws.send_json({'op': 'key', 'key': 'home'}); await asyncio.sleep(0.05)
            assert {'op': 'key', 'key': 'home'} in commands
            server.push(dict(event='video_config', codec='avc1.42C01E', width=960, height=540))
            assert (await ws.receive_json())['event'] == 'video_config'
            server.push(dict(event='video', data=b'\0\0\0\1\x65test', key=True, time_us=1))
            frame = await ws.receive()
            assert frame.type == aiohttp.WSMsgType.BINARY and b'test' in frame.data
            await ws.send_json({'op': 'bt_data', 'data': 'forbidden'})
            assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
            await ws.close()
    try:
        asyncio.run(check())
        assert (tmp_path / 'web-token').stat().st_mode & 0o077 == 0
    finally: server.close()
