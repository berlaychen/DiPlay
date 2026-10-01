"""Real browser + backend synthetic AVC/PCM smoke; never an iPhone test."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'build/smoke'
OUTPUT.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
with tempfile.TemporaryDirectory(prefix='diplay-web-smoke-') as temporary:
    directory=Path(temporary); state=directory/'state'; config=directory/'config.toml'
    config.write_text(f'state_dir="{state}"\n[video]\nwidth=640\nheight=360\nfps=30\n[web]\nhost="127.0.0.1"\nport={port}\n')
    with (OUTPUT/'web.log').open('w') as logfile:
        process=subprocess.Popen([str(ROOT/'diplay'),'--demo','--mode','web','--config',str(config),
                                  '--duration','35','--stats',str(OUTPUT/'web-stats.json')],stdout=logfile,stderr=logfile)
        try:
            origin=f'http://127.0.0.1:{port}'
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Backend exited; inspect web.log')
                try:
                    urllib.request.urlopen(origin+'/health',timeout=0.2).read();break
                except OSError: time.sleep(0.1)
            else: raise RuntimeError('Backend did not start')
            errors=[]
            with sync_playwright() as pw:
                browser=pw.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
                page=browser.new_page(viewport={'width':1000,'height':720})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin)
                page.locator('#token').fill((state/'web-token').read_text().strip())
                page.locator('form button').click()
                page.wait_for_function('videoCount >= 15',timeout=20000)
                page.locator('#audio').click()
                page.wait_for_function('audioContext?.state === "running"')
                page.locator('canvas').click(position={'x':200,'y':150})
                page.locator('[data-key="home"]').click()
                page.wait_for_timeout(1500)
                assert 'DEMO' in page.locator('#state').inner_text()
                assert page.evaluate('context.getImageData(0,0,canvas.width,canvas.height).data.some((x,i)=>i%4!==3&&x>30)')
                assert not errors,errors
                page.screenshot(path=str(OUTPUT/'web-preview.png'))
                (OUTPUT/'browser.json').write_text(json.dumps(page.evaluate('({frames:videoCount,audio:audioContext.state,codec:configuration.codec})'),indent=2))
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    stats=json.loads((OUTPUT/'web-stats.json').read_text())
    assert stats['errors']==0,stats
    assert stats['audio_rtp_packets']>10,stats
    assert stats['video_frames_received']>=15,stats
    assert not stats['physical_session_observed']
print('PASS browser H.264 rendering, local audio, controls, demo isolation')
