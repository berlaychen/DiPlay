"""Real GTK/GStreamer demo validation under Xvfb; not an iPhone session."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import pytest


def test_native_settings_with_real_gtk_and_gstreamer():
    if not importlib.util.find_spec('gi') or not shutil.which('xvfb-run'):
        pytest.skip('GTK/Xvfb not installed locally; Linux CI installs both')
    subprocess.run(['xvfb-run','-a',sys.executable,__file__,'--probe'],check=True,timeout=40)


def probe():
    from diplay_linux.config import Config, ROOT
    from diplay_linux.app import Application
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import GLib, Gtk, Gdk
    errors=[];phase=0;deadline=time.monotonic()+25
    with tempfile.TemporaryDirectory(prefix='diplay-native-ui-') as directory:
        cfg=Config({'state_dir':directory,'test_audio_sink':True,
                    'video':dict(width=640,height=360,fps=30,decoder='avdec_h264')},'native')
        app=Application(cfg,SimpleNamespace(demo=True,duration=0,stats=None))
        view=app.frontend
        def visit(widget):
            yield widget
            if isinstance(widget,Gtk.Container):
                for child in widget.get_children():yield from visit(child)
        def check():
            nonlocal phase
            try:
                if time.monotonic()>deadline:raise AssertionError('Native presentation smoke timed out')
                if app.media.video_frames_rendered < 5:return True
                if phase==0:
                    view._settings(None)
                    widgets=list(visit(view.settings_dialog))
                    combo=next(w for w in widgets if isinstance(w,Gtk.ComboBoxText))
                    combo.set_active_id('480p')
                    scales=[w for w in widgets if isinstance(w,Gtk.Scale)]
                    assert len(scales)==5
                    scales[0].set_value(40)
                    view.settings_dialog.response(Gtk.ResponseType.APPLY)
                    phase=1
                    return True
                caps=app.media.video.get_by_name('display').get_static_pad('sink').get_current_caps()
                if caps is None or caps.get_structure(0).get_value('width')!=800:return True
                assert app.media.video_widget.get_parent() is view.input
                assert view.settings_dialog is None and cfg.document['video']['fps']==30
                assert abs(next(iter(app.media.audio.values()))[3].get_property('volume')-.4)<.0001
                saved=json.loads((Path(directory)/'presentation.json').read_text())
                assert saved['preset']=='480p' and saved['volumes']['master']==.4
                messages=[]
                view.contacts.send=messages.append
                for kind in (Gdk.EventType.TOUCH_BEGIN,Gdk.EventType.TOUCH_END):
                    view._touch(None,SimpleNamespace(sequence='first',type=kind,x=100,y=100))
                assert [m['contacts'][0]['down'] for m in messages]==[True,False]
                view._touch(None,SimpleNamespace(sequence='first',type=Gdk.EventType.TOUCH_BEGIN,x=100,y=100))
                view._touch(None,SimpleNamespace(sequence='second',type=Gdk.EventType.TOUCH_BEGIN,x=150,y=150))
                assert all(p['down'] for p in messages[-1]['contacts'])
                view._cancel()
                assert all(not p['down'] for p in messages[-1]['contacts'])
                output=ROOT/'build/smoke';output.mkdir(parents=True,exist_ok=True)
                (output/'native-settings.json').write_text(json.dumps(dict(
                    preset='480p',actual_width=800,master_volume=.4,contacts=2,
                    physical_carplay=False,gtk_gstreamer=True),indent=2))
                phase=2;app.stop();return False
            except BaseException as error:
                errors.append(error);app.stop();return False
        GLib.timeout_add(100,check)
        assert app.run()==0
        if errors:raise errors[0]
        assert phase==2

if __name__=='__main__' and '--probe' in sys.argv:probe()
