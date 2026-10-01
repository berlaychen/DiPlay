"""Two stable contact slots; only motion is coalesced, never press/release edges."""
import math


def normalized(x, y, width, height, aspect):
    if not all(math.isfinite(n) for n in (x, y, width, height, aspect)) or min(width, height, aspect) <= 0:
        return None
    w, h = min(width, height * aspect), min(height, width / aspect)
    return (max(0.0, min(1.0, (x - (width - w) / 2) / w)),
            max(0.0, min(1.0, (y - (height - h) / 2) / h)))


class Contacts:
    def __init__(self, send, schedule, cancel):
        self.send, self.schedule, self.cancel_timer = send, schedule, cancel
        self.ids = [None, None]
        self.points = [dict(x=0.0, y=0.0, down=False) for _ in range(2)]
        self.pending = None

    def _cancel(self):
        if self.pending is not None:
            self.cancel_timer(self.pending)
            self.pending = None

    def flush(self):
        self.pending = None
        self.send(dict(op='touch', contacts=[dict(p) for p in self.points]))
        return False

    def edge(self, ident, x, y, down):
        if ident is None or not all(math.isfinite(n) and 0 <= n <= 1 for n in (x, y)):
            return
        if ident not in self.ids:
            if not down or None not in self.ids:
                return
            slot = self.ids.index(None)
            self.ids[slot] = ident
        else:
            slot = self.ids.index(ident)
        self.points[slot] = dict(x=x, y=y, down=down)
        self._cancel()
        self.flush()
        if not down:
            self.ids[slot] = None

    def move(self, ident, x, y):
        if ident not in self.ids or ident is None or not all(math.isfinite(n) and 0 <= n <= 1 for n in (x, y)):
            return
        self.points[self.ids.index(ident)] = dict(x=x, y=y, down=True)
        if self.pending is None:
            self.pending = self.schedule(25, self.flush)

    def release_all(self):
        self._cancel()
        active = any(p['down'] for p in self.points)
        self.ids = [None, None]
        for p in self.points:
            p['down'] = False
        if active:
            self.flush()
