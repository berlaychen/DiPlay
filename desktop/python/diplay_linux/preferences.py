"""Explicit, bounded presentation settings shared by both Linux frontends.

Only user Apply/slider-change actions write this small file. No secrets, network
settings, pipeline strings or browser-supplied paths are accepted.
"""
import json
import math
import os
from pathlib import Path
import stat
import tempfile

PRESETS = {'480p': (800, 480, 30), '540p': (960, 540, 30), '720p': (1280, 720, 30)}
VOLUMES = ('master', 'media', 'guidance', 'speech', 'telephony')


def validate_preference(value):
    op = value.get('op')
    if op == 'presentation':
        if set(value) - {'op', 'preset', 'volumes'}:
            raise ValueError('Unknown presentation field')
        levels = value.get('volumes', {})
        if not isinstance(levels, dict) or len(levels) > len(VOLUMES):
            raise ValueError('Invalid volumes')
        result = dict(op=op, volumes={})
        for role, level in levels.items():
            result['volumes'][role] = validate_preference(dict(op='volume', role=role, value=level))['value']
        if 'preset' in value:
            result['preset'] = validate_preference(dict(op='display_preset', preset=value['preset']))['preset']
        return result
    if op == 'display_preset' and isinstance(value.get('preset'), str) and value['preset'] in PRESETS:
        return dict(op=op, preset=value['preset'])
    if op == 'volume' and isinstance(value.get('role'), str) and value['role'] in VOLUMES:
        level = value.get('value')
        if type(level) in (int, float) and math.isfinite(level) and 0 <= level <= 1:
            return dict(op=op, role=value['role'], value=float(level))
    raise ValueError('Invalid presentation setting')


def role_group(role):
    if role == 'guidance':
        return 'guidance'
    if role == 'telephony':
        return 'telephony'
    if role in ('speechrecognition', 'speech', 'alert'):
        return 'speech'
    return 'media'


class Preferences:
    def __init__(self, config):
        self.config = config
        self.path = config.state_dir / 'presentation.json'
        self.preset = None
        self.volumes = dict.fromkeys(VOLUMES, 1.0)
        if self.path.exists() or self.path.is_symlink():
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, 'r') as stream:
                meta = os.fstat(stream.fileno())
                if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_size > 2048:
                    raise ValueError('presentation.json must be a small owner-only regular file')
                data = json.load(stream)
            if not isinstance(data, dict) or set(data) - {'version', 'preset', 'volumes'} or data.get('version') != 1:
                raise ValueError('Unsupported presentation settings')
            if data.get('preset') is not None:
                self.preset = validate_preference(dict(op='display_preset', preset=data['preset']))['preset']
            levels = data.get('volumes', {})
            if not isinstance(levels, dict):
                raise ValueError('Invalid volumes')
            for role, level in levels.items():
                self.volumes[role] = validate_preference(dict(op='volume', role=role, value=level))['value']
        self._apply()

    def _apply(self):
        if self.preset:
            width, height, fps = PRESETS[self.preset]
            self.config.document['video'].update(width=width, height=height, fps=fps)
        self.config.document['audio']['volumes'] = dict(self.volumes)

    def update(self, value):
        command = validate_preference(value)
        preset, volumes = self.preset, dict(self.volumes)
        if command['op'] == 'presentation':
            preset = command.get('preset', preset)
            volumes.update(command['volumes'])
        elif command['op'] == 'display_preset':
            preset = command['preset']
        else:
            volumes[command['role']] = command['value']
        if (preset, volumes) == (self.preset, self.volumes):
            return False
        # Persist before touching the running configuration; failure leaves it unchanged.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.presentation-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(version=1, preset=preset, volumes=volumes), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.preset, self.volumes = preset, volumes
        self._apply()
        return True

    def event(self):
        v = self.config.document['video']
        return dict(event='preferences', version=1, preset=self.preset or 'custom',
                    width=v['width'], height=v['height'], fps=v['fps'], codec='h264',
                    volumes=dict(self.volumes))
