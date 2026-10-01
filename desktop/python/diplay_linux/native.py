import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
from .controls import Contacts, normalized
from .preferences import PRESETS, VOLUMES


class NativeFrontend:
    def __init__(self, media, command, quit_app):
        self.media, self.command = media, command
        self.enabled = False
        self.settings_dialog = None
        self.presentation = {}
        self.contacts = Contacts(command, GLib.timeout_add, GLib.source_remove)
        self.window = Gtk.Window(title='DiPlay Linux - preview')
        self.window.set_default_size(960, 620)
        self.window.connect('destroy', lambda *a: (self.release_input(), quit_app()))
        self.window.connect('key-press-event', self._key)
        self.window.connect('focus-out-event', self._cancel)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(box)
        self.label = Gtk.Label(label='Starting receiver - no iPhone connection yet')
        self.label.set_margin_top(8)
        self.label.set_margin_bottom(8)
        box.pack_start(self.label, False, False, 0)
        self.input = Gtk.EventBox()
        self.input.set_above_child(True)
        self.input.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                              Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
        self.input.connect('button-press-event', self._press)
        self.input.connect('button-release-event', self._release)
        self.input.connect('motion-notify-event', self._motion)
        self.input.connect('touch-event', self._touch)
        self.input.connect('grab-broken-event', self._cancel)
        if media.video_widget.get_parent() is not None:
            raise RuntimeError('Video sink was started before native widget embedding')
        self.input.add(media.video_widget)
        box.pack_start(self.input, True, True, 0)
        controls = Gtk.Box(spacing=8)
        controls.set_margin_top(8)
        controls.set_margin_bottom(8)
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
        settings = Gtk.Button(label='Settings')
        settings.connect('clicked', self._settings)
        controls.pack_start(settings, True, True, 0)
        box.pack_start(controls, False, False, 0)
        self.window.show_all()
        if media.video_widget.get_parent() is not self.input:
            raise RuntimeError('Video widget must share the touch input container')
        media.start_native()

    def _position(self, event):
        allocation = self.input.get_allocation()
        video = self.media.config.document['video']
        return normalized(event.x, event.y, allocation.width, allocation.height,
                          video['width'] / video['height'])

    def _mouse_edge(self, event, down):
        if not self.enabled or event.button != 1 or any(i not in (None, 'mouse') for i in self.contacts.ids):
            return True
        position = self._position(event)
        if position is not None:
            self.contacts.edge('mouse', *position, down)
        return True

    def _press(self, widget, event):
        return self._mouse_edge(event, True)

    def _release(self, widget, event):
        return self._mouse_edge(event, False)

    def _motion(self, widget, event):
        position = self._position(event)
        if self.enabled and position is not None:
            self.contacts.move('mouse', *position)
        return True

    def _touch(self, widget, event):
        if not self.enabled:
            return True
        position = self._position(event)
        if position is None:
            return True
        if event.type == Gdk.EventType.TOUCH_BEGIN:
            self.contacts.edge(event.sequence, *position, True)
        elif event.type == Gdk.EventType.TOUCH_UPDATE:
            self.contacts.move(event.sequence, *position)
        elif event.type in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            self.contacts.edge(event.sequence, *position, False)
        return True

    def release_input(self):
        self.contacts.release_all()

    def _cancel(self, *args):
        self.release_input()
        return False

    def _key(self, widget, event):
        if self.settings_dialog is not None or not self.enabled:
            return False
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        name = Gdk.keyval_name(event.keyval)
        if name == 'Escape':
            self.window.unfullscreen()
            return True
        if name in ('Left', 'Right', 'Up', 'Down', 'Return'):
            self.command(dict(op='key', key='select' if name == 'Return' else name.lower()))
            return True
        return False

    def _settings(self, button):
        self.release_input()
        if self.settings_dialog is not None:
            self.settings_dialog.present()
            return
        dialog = Gtk.Dialog(title='Low-resource settings', transient_for=self.window, modal=True)
        self.settings_dialog = dialog
        dialog.add_button('Cancel', Gtk.ResponseType.CANCEL)
        dialog.add_button('Apply', Gtk.ResponseType.APPLY)
        area = dialog.get_content_area()
        area.set_spacing(8)
        area.set_border_width(16)
        area.pack_start(Gtk.Label(label='H.264 / 30 fps. Display changes reconnect the phone.'), False, False, 0)
        presets = Gtk.ComboBoxText()
        presets.append('custom', 'Keep current resolution')
        for key, (width, height, fps) in PRESETS.items():
            presets.append(key, f'{width} x {height} / {fps} fps')
        presets.set_active_id(self.presentation.get('preset', 'custom'))
        area.pack_start(presets, False, False, 0)
        levels = {}
        for role in VOLUMES:
            row = Gtk.Box(spacing=8)
            row.pack_start(Gtk.Label(label=role.title()), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            scale.set_value(self.presentation.get('volumes', {}).get(role, 1.0) * 100)
            scale.set_hexpand(True)
            row.pack_start(scale, True, True, 0)
            area.pack_start(row, False, False, 0)
            levels[role] = scale
        def response(widget, result):
            if result == Gtk.ResponseType.APPLY:
                update = dict(op='presentation', volumes={r: s.get_value() / 100 for r, s in levels.items()})
                preset = presets.get_active_id()
                if preset in PRESETS:
                    update['preset'] = preset
                self.command(update)
            self.settings_dialog = None
            widget.destroy()
        dialog.connect('response', response)
        dialog.connect('destroy', lambda *a: setattr(self, 'settings_dialog', None))
        dialog.show_all()

    def status(self, event):
        kind = event.get('event')
        if kind == 'preferences':
            self.presentation = event
        elif kind == 'recovery' and event['state'] in ('backoff', 'blocked'):
            self.label.set_text('Retry pending' if event['state'] == 'backoff' else 'Reconnect required: check setup/cable/phone')
        elif kind == 'status':
            state = event.get('state', '')
            if state == 'connected' or state.startswith('DEMO'):
                self.enabled = True
            elif state in ('disconnected', 'transport_error', 'preparing_usb', 'waiting_for_phone',
                           'usb_error_reconnect_required', 'usb_disconnected_reconnect_required'):
                self.release_input()
                self.enabled = False
            self.label.set_text(event.get('transport', '').upper() + ' | ' + event.get('state', ''))
        elif kind in ('error', 'fatal'):
            self.label.set_text(event.get('component', kind) + ': ' + event.get('message', '')[:220])
        elif kind == 'decoder':
            self.window.set_title('DiPlay Linux - ' + event['name'] + ' - preview')
