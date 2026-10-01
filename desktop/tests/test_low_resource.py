"""Deterministic low-resource regressions. No CarPlay hardware success is implied."""
import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from diplay_linux.config import Config
from diplay_linux.controls import Contacts, normalized
from diplay_linux.preferences import Preferences, role_group
from diplay_linux.protocol import EventQueue, validate_control
from diplay_linux.supervisor import Supervisor
from diplay_linux.app import Application


class Clock:
    now = 0
    def __call__(self): return self.now


def test_retry_is_finite_exponential_and_duplicate_errors_do_not_postpone():
    clock = Clock(); s = Supervisor(clock=clock)
    s.begin()
    for attempt, delay in enumerate((3, 6, 12, 24, 48), 1):
        s.failed(); deadline = s.deadline
        clock.now += 1; s.failed()
        assert s.deadline == deadline
        assert not s.tick()
        clock.now = deadline
        assert s.tick() and s.attempts == attempt
    s.failed()
    assert s.state == 'blocked'
    clock.now += 10000
    assert not s.tick()
    s.begin(manual=True)
    assert s.attempts == 0


def test_active_static_map_does_not_trigger_no_video_disconnect():
    clock = Clock(); s = Supervisor(clock=clock); s.begin()
    s.observe(dict(event='status', state='connected'))
    s.observe(dict(event='video'))
    clock.now = 100000
    assert not s.tick() and s.state == 'active'
    s.observe(dict(event='status', state='authenticated'))
    assert s.state == 'active'


def test_session_object_or_authentication_is_not_initial_video_success():
    clock = Clock(); s = Supervisor(clock=clock); s.begin()
    s.observe(dict(event='status', state='authenticated'))
    s.observe(dict(event='status', state='connected'))
    clock.now = 90
    assert not s.tick() and s.state == 'backoff'


def test_retry_budget_resets_only_after_stable_session():
    clock = Clock(); s = Supervisor(clock=clock); s.begin()
    s.failed(); clock.now = 3; assert s.tick()
    s.observe(dict(event='video')); s.observe(dict(event='status', state='connected'))
    clock.now = 32; s.tick(); assert s.attempts == 1
    clock.now = 33; s.tick(); assert s.attempts == 0
    s.pause(); clock.now = 9999
    assert not s.tick()


@pytest.mark.parametrize('options', [{'auto_reconnect':'yes'}, {'retry_limit':-1},
    {'retry_initial':0}, {'retry_max':1}, {'startup_timeout':True}, {'stable_seconds':1}])
def test_invalid_retry_settings_fail_closed(options):
    with pytest.raises(ValueError): Supervisor(options)


def test_retry_disabled_and_permanent_failure():
    for options, permanent in (({'auto_reconnect':False}, False), ({}, True)):
        s = Supervisor(options); s.begin(); s.failed(permanent)
        assert s.state == 'blocked'


def touch_harness():
    sent, timers = [], {}
    next_id = iter(range(1, 1000))
    def schedule(ms, callback):
        assert ms == 25
        ident = next(next_id); timers[ident] = callback; return ident
    return Contacts(sent.append, schedule, lambda ident: timers.pop(ident, None)), sent, timers


def test_fast_tap_always_delivers_both_edges_without_timer():
    c, sent, timers = touch_harness()
    c.edge('finger', .5, .5, True); c.edge('finger', .5, .5, False)
    assert [m['contacts'][0]['down'] for m in sent] == [True, False]
    assert not timers


def test_two_contact_slots_do_not_shift_when_first_finger_lifts():
    c, sent, timers = touch_harness()
    c.edge('first', .1, .2, True); c.edge('second', .6, .7, True)
    c.edge('third', .9, .9, True)
    assert len(sent) == 2
    c.edge('first', .1, .2, False)
    assert sent[-1]['contacts'] == [dict(x=.1,y=.2,down=False), dict(x=.6,y=.7,down=True)]
    c.move('second', .9, .8); c.release_all()
    assert not timers and all(not p['down'] for p in sent[-1]['contacts'])


def test_motion_bursts_use_one_callback_and_keep_latest_position():
    c, sent, timers = touch_harness(); c.edge(1, 0, 0, True)
    for n in range(100): c.move(1, n / 100, .5)
    assert len(timers) == 1 and len(sent) == 1
    callback = timers.pop(next(iter(timers))); assert callback() is False
    assert sent[-1]['contacts'][0]['x'] == .99


def test_letterbox_mapping_and_zero_dimension():
    assert normalized(500, 300, 1000, 600, 16/9) == (.5, .5)
    assert normalized(0, 0, 1000, 1000, 16/9) == (0, 0)
    assert normalized(1, 2, 0, 600, 16/9) is None
    assert normalized(float('nan'), 2, 100, 600, 16/9) is None


@pytest.mark.parametrize('mode', ['native','web'])
@pytest.mark.parametrize('transport', ['wired','wireless'])
def test_same_preferences_for_all_four_combinations(tmp_path, mode, transport):
    cfg=Config({'state_dir':str(tmp_path), 'connection':{'transport':transport}}, mode)
    prefs=Preferences(cfg)
    assert prefs.update(dict(op='presentation',preset='720p',volumes={'master':.8,'guidance':.4}))
    assert cfg.document['video']['width']==1280 and cfg.document['video']['fps']==30
    assert cfg.document['audio']['volumes']['guidance']==.4
    assert prefs.path.stat().st_mode & 0o077 == 0
    assert not prefs.update(dict(op='presentation',preset='720p',volumes={'master':.8,'guidance':.4}))
    again=Preferences(Config({'state_dir':str(tmp_path)}, mode))
    assert again.event()['volumes']['master']==.8
    assert set(json.loads(prefs.path.read_text())) == {'version','preset','volumes'}


@pytest.mark.parametrize('value', [dict(op='volume',role='master',value=float('nan')),
    dict(op='volume',role='master',value=True), dict(op='volume',role='master',value=2),
    dict(op='volume',role='secret',value=1), dict(op='display_preset',preset='4k'),
    dict(op='display_preset',preset=[]), dict(op='presentation',preset='1080p',volumes={'master':.3}),
    dict(op='presentation',volumes={'master':.3},auth_dir='/tmp/key')])
def test_settings_whitelist_and_atomic_rejection(tmp_path, value):
    prefs=Preferences(Config({'state_dir':str(tmp_path)}))
    with pytest.raises(ValueError): prefs.update(value)
    assert prefs.preset is None and prefs.volumes['master']==1 and not prefs.path.exists()
    with pytest.raises(ValueError): validate_control(value)


def test_preferences_failed_write_does_not_mutate_runtime(tmp_path, monkeypatch):
    prefs=Preferences(Config({'state_dir':str(tmp_path)}))
    monkeypatch.setattr('diplay_linux.preferences.os.replace', Mock(side_effect=OSError('read-only')))
    with pytest.raises(OSError): prefs.update(dict(op='display_preset',preset='480p'))
    assert prefs.config.document['video']['width']==960 and not list(tmp_path.glob('.presentation-*'))


def test_preferences_reject_symlink_and_public_files(tmp_path):
    cfg=Config({'state_dir':str(tmp_path)})
    target=tmp_path/'other';target.write_text('{}')
    link=tmp_path/'presentation.json';link.symlink_to(target)
    with pytest.raises(OSError): Preferences(cfg)
    link.unlink();link.write_text('{}');link.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): Preferences(cfg)


def test_volume_roles_do_not_change_microphone_settings():
    assert [role_group(r) for r in ('media','default','guidance','speechrecognition','telephony')] == [
        'media','media','guidance','speech','telephony']


def broker(tmp_path, mode='native', transport='wireless'):
    callbacks=[]
    a=Application.__new__(Application)
    a.config=Config({'state_dir':str(tmp_path),'connection':{'transport':transport}},mode)
    a.preferences=Preferences(a.config)
    a.args=SimpleNamespace(demo=False)
    a.queue=EventQueue();a.drain_lock=threading.Lock();a.drain_pending=False
    a.pending_controls={};a.control_pending=False;a.last_presentation=a.last_reconnect=-100
    a.stopping=False;a.stats={'events':0,'errors':0};a.generation=1;a.connected_once=False
    a.GLib=SimpleNamespace(idle_add=lambda fn,*args: callbacks.append((fn,args)), timeout_add=Mock())
    a.supervisor=Supervisor();a.supervisor.begin();a.recovery_state=None
    a.frontend=Mock();a.web=Mock();a.media=Mock();a.core=Mock();a.radio=a.usb=None
    return a, callbacks


def test_event_driven_broker_coalesces_bursts_and_is_one_shot(tmp_path):
    a, callbacks=broker(tmp_path)
    threads=[threading.Thread(target=lambda: a.emit(dict(event='diagnostic',message='test'))) for _ in range(40)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert len(callbacks)==1
    fn,args=callbacks.pop();assert fn(*args) is False
    assert a.stats['events']==40 and not callbacks and not a.drain_pending
    a.emit(dict(event='diagnostic',message='later'));assert len(callbacks)==1


def test_old_generation_errors_cannot_restart_a_new_connection(tmp_path):
    a, callbacks=broker(tmp_path)
    a.emit(dict(event='status',state='disconnected',_generation=0))
    a.drain()
    assert a.supervisor.state=='starting'
    a.emit(dict(event='status',state='disconnected',_generation=1))
    a.drain()
    assert a.supervisor.state=='backoff'


@pytest.mark.parametrize('mode',['native','web'])
@pytest.mark.parametrize('transport',['wired','wireless'])
def test_display_and_audio_controls_reuse_same_backend(tmp_path, mode, transport):
    a, callbacks=broker(tmp_path,mode,transport);a.reconnect=Mock()
    a.control(dict(op='presentation',preset='480p',volumes={'master':.5}))
    fn,args=callbacks.pop(0);assert fn(*args) is False
    a.reconnect.assert_called_once()
    a.media._duck.assert_called_once()
    assert a.config.document['video']['width']==800
    a.reconnect.reset_mock()
    a._presentation(dict(op='volume',role='guidance',value=.5))
    a.reconnect.assert_not_called()


def test_control_bursts_are_bounded_and_manual_disconnect_pauses_retry(tmp_path):
    a, callbacks=broker(tmp_path);a.reconnect=Mock()
    for _ in range(100):a.control(dict(op='reconnect'))
    assert len(callbacks)==1 and len(a.pending_controls)==1
    fn,args=callbacks.pop(0);fn(*args);a.reconnect.assert_called_once()
    a.control(dict(op='disconnect'));fn,args=callbacks.pop(0);fn(*args)
    assert a.supervisor.state=='paused'


def test_external_ap_is_configuration_only_and_both_bands_are_accepted(tmp_path):
    for channel in (6,36):
        cfg=Config({'network':dict(topology='external',interface='eth0',phone='01:02:03:04:05:06',
            ssid='Car network',password='12345678',address='192.168.8.20',channel=channel)})
        assert cfg.network_settings()['channel']==channel


def test_javascript_regressions_run_in_ci():
    if not shutil.which('node'):pytest.skip('Node not present')
    root=Path(__file__).parent
    subprocess.run(['node','--test',str(root/'test_web_client.cjs'),str(root/'test_input.cjs')],check=True,timeout=30)
