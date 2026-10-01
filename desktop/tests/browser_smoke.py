"""Real codec-enabled Chrome + backend synthetic AVC/PCM test; not an iPhone test.

Production CSP stays enabled. Hardware preference may be unsupported on CI;
the real client probes a usable fallback rather than claiming GPU acceleration.
"""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'build/smoke'
OUTPUT.mkdir(parents=True, exist_ok=True)
subprocess.run(['node', '--test', str(ROOT/'tests/test_web_client.cjs')], check=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
with tempfile.TemporaryDirectory(prefix='diplay-web-smoke-') as temporary:
    directory=Path(temporary); state=directory/'state'; config=directory/'config.toml'
    config.write_text(f'state_dir="{state}"\n[video]\nwidth=640\nheight=360\nfps=30\n[web]\nhost="127.0.0.1"\nport={port}\n')
    with (OUTPUT/'web.log').open('w') as logfile:
        process=subprocess.Popen([str(ROOT/'diplay'),'--demo','--mode','web','--config',str(config),
                                  '--duration','45','--stats',str(OUTPUT/'web-stats.json')],stdout=logfile,stderr=logfile)
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
                browser=pw.chromium.launch(channel='chrome',args=['--autoplay-policy=no-user-gesture-required'])
                page=browser.new_page(viewport={'width':1000,'height':720})
                page.on('pageerror',lambda error:errors.append(str(error)))
                try:
                    page.goto(origin)
                    support=page.evaluate('''async () => {
                      const result={};
                      for(const hardwareAcceleration of ['prefer-hardware','no-preference','prefer-software']){
                        result[hardwareAcceleration]=await VideoDecoder.isConfigSupported({codec:'avc1.42C01E',optimizeForLatency:true,hardwareAcceleration});
                      }
                      return result;
                    }''')
                    (OUTPUT/'browser-codecs.json').write_text(json.dumps(support,indent=2))
                    page.locator('#token').fill((state/'web-token').read_text().strip())
                    page.locator('form button').click()
                    deadline=time.monotonic()+20
                    while int(page.locator('#counter').inner_text().split()[0])<15:
                        if time.monotonic()>deadline:
                            raise RuntimeError('Video timeout: '+page.locator('#diagnostic').inner_text())
                        page.wait_for_timeout(100)
                    page.locator('#audio').click()
                    expect(page.locator('#audio')).to_have_text('Sound enabled',timeout=10000)
                    page.evaluate('() => { window.testAnalyser=audioContext.createAnalyser(); playback.connect(testAnalyser); }')
                    page.locator('canvas').click(position={'x':200,'y':150})
                    page.locator('[data-key="home"]').click()
                    page.wait_for_timeout(1500)
                    assert 'DEMO' in page.locator('#state').inner_text()
                    assert page.evaluate('() => context.getImageData(0,0,canvas.width,canvas.height).data.some((x,i)=>i%4!==3&&x>30)')
                    audio_peak=page.evaluate('() => {const s=new Float32Array(testAnalyser.fftSize);testAnalyser.getFloatTimeDomainData(s);return Math.max(...s.map(Math.abs));}')
                    assert audio_peak>0.001, 'AudioWorklet did not produce the synthetic tone'
                    # WebCodecs closes a decoder on terminal errors. Simulate that lifecycle,
                    # then require the real client to recreate it and render more actual AVC.
                    page.evaluate('() => { decoder.close(); recoverVideo(new Error("test: closed decoder")); }')
                    # Playwright's string polling uses eval and violates production CSP.
                    # Poll with a function through the debugger; do not weaken script-src.
                    recovery_deadline = time.monotonic() + 15
                    while not page.evaluate('() => decoder?.state === "configured" && videoCount >= 15'):
                        if time.monotonic() >= recovery_deadline:
                            raise RuntimeError('Decoder recovery timeout: ' + page.locator('#diagnostic').inner_text())
                        page.wait_for_timeout(100)
                    assert page.evaluate('decoderRecoveries') == 1
                    assert not errors,errors
                    metrics=page.evaluate('() => ({frames:videoCount,audio:audioContext.state,codec:configuration.codec,decoderPreference:decoderOptions.hardwareAcceleration})')
                    metrics['decoder_recovery_verified']=True
                    metrics['audio_peak']=audio_peak
                    metrics['browser']=browser.version
                    (OUTPUT/'browser.json').write_text(json.dumps(metrics,indent=2))
                finally:
                    page.screenshot(path=str(OUTPUT/'web-preview.png'))
                    (OUTPUT/'browser-errors.json').write_text(json.dumps(errors,indent=2))
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
print('PASS browser H.264 pixels, AudioWorklet tone, controls, strict CSP and demo isolation')
