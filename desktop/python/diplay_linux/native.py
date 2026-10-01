import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk


class NativeFrontend:
    def __init__(self, media, command, quit_app):
        self.media, self.command = media, command
        self.down = False
        self.touch_sequence = None
        self.window = Gtk.Window(title='DiPlay Linux - preview')
        self.window.set_default_size(960, 620)
        self.window.connect('destroy', lambda *a: quit_app())
        self.window.connect('key-press-event', self._key)
        self.window.connect('focus-out-event', self._cancel)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(box)
        self.label = Gtk.Label(label='Starting receiver - no iPhone connection yet')
        self.label.set_margin_top(8); self.label.set_margin_bottom(8)
        box.pack_start(self.label, False, False, 0)
        self.input = Gtk.EventBox()
        self.input.set_above_child(True)
        self.input.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                              Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
        self.input.connect('button-press-event', self._press)
        self.input.connect('button-release-event', self._release)
        self.input.connect('motion-notify-event', self._motion)
        self.input.connect('touch-event', self._touch)
        self.input.add(media.video_widget)
        box.pack_start(self.input, True, True, 0)
        controls = Gtk.Box(spacing=8)
        controls.set_margin_top(8); controls.set_margin_bottom(8)
        for text, key in [('Home', 'home'), ('Back', 'back'), ('Siri', 'siri')]:
            button = Gtk.Button(label=text)
            button.set_size_request(90, 44)
            button.connect('clicked', lambda b, k=key: command(dict(op='key', key=k)))
            controls.pack_start(button, True, True, 0)
        reconnect = Gtk.Button(label='Reconnect')
        reconnect.connect('clicked', lambda b: command(dict(op='reconnect')))
        controls.pack_start(reconnect, True, True, 0)
        full = Gtk.Button(label='Fullscreen')
        full.connect('clicked', lambda b: self.window.fullscreen())
        controls.pack_start(full, True, True, 0)
        box.pack_start(controls, False, False, 0)
        self.window.show_all()

    def _position(self, event):
        allocation = self.input.get_allocation()
        ratio = self.media.config.document['video']['width'] / self.media.config.document['video']['height']
        width = allocation.width; height = width / ratio
        if height > allocation.height:
            height = allocation.height; width = height * ratio
        if not width or not height:
            return 0.0, 0.0
        return (max(0.0, min(1.0, (event.x - (allocation.width - width) / 2) / width)),
                max(0.0, min(1.0, (event.y - (allocation.height - height) / 2) / height)))

    def _send(self, event, down):
        x, y = self._position(event)
        self.command(dict(op='touch', contacts=[dict(x=x, y=y, down=down)]))

    def _press(self, widget, event):
        if self.touch_sequence is None and event.button == 1:
            self.down = True; self._send(event, True)
        return True

    def _release(self, widget, event):
        if self.touch_sequence is None and self.down:
            self.down = False; self._send(event, False)
        return True

    def _motion(self, widget, event):
        if self.down and self.touch_sequence is None:
            self._send(event, True)
        return True

    def _touch(self, widget, event):
        if event.type == Gdk.EventType.TOUCH_BEGIN and self.touch_sequence is None:
            self.touch_sequence = event.sequence; self._send(event, True)
        elif event.sequence == self.touch_sequence:
            if event.type == Gdk.EventType.TOUCH_UPDATE:
                self._send(event, True)
            elif event.type in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
                self._send(event, False); self.touch_sequence = None
        return True

    def _cancel(self, *args):
        if self.down or self.touch_sequence:
            self.command(dict(op='touch', contacts=[dict(x=0.0, y=0.0, down=False)]))
        self.down = False; self.touch_sequence = None
        return False

    def _key(self, widget, event):
        name = Gdk.keyval_name(event.keyval)
        if name == 'Escape':
            self.window.unfullscreen(); return True
        if name in ('Left', 'Right', 'Up', 'Down', 'Return'):
            self.command(dict(op='key', key='select' if name == 'Return' else name.lower())); return True
        return False

    def status(self, event):
        kind = event.get('event')
        if kind == 'status':
            self.label.set_text(event.get('state', ''))
        elif kind in ('error', 'fatal'):
            self.label.set_text(event.get('component', kind) + ': ' + event.get('message', '')[:220])
        elif kind == 'decoder':
            self.window.set_title('DiPlay Linux - ' + event['name'] + ' - preview')
