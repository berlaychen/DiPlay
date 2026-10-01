"""Small, monotonic, finite reconnect policy. No threads, polling I/O or radio changes."""
import time


class Supervisor:
    def __init__(self, options=None, clock=time.monotonic):
        values = dict(options or {})
        self.enabled = values.get('auto_reconnect', True)
        if type(self.enabled) is not bool:
            raise ValueError('connection.auto_reconnect must be boolean')
        for key, default, low, high in (
            ('retry_limit', 5, 0, 10), ('retry_initial', 3, 1, 60),
            ('retry_max', 60, 1, 300), ('startup_timeout', 90, 30, 300),
            ('stable_seconds', 30, 10, 300),
        ):
            value = values.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'connection.{key} must be in {low}..{high}')
            setattr(self, key, value)
        if self.retry_max < self.retry_initial:
            raise ValueError('retry_max must not be smaller than retry_initial')
        self.clock = clock
        self.attempts = 0
        self.state = 'paused'
        self.deadline = None
        self.stable_at = None
        self.connected = False
        self.video_seen = False

    def begin(self, manual=False):
        if manual:
            self.attempts = 0
        self.state = 'starting'
        self.connected = self.video_seen = False
        self.stable_at = None
        self.deadline = self.clock() + self.startup_timeout

    def observe(self, event):
        if self.state not in ('starting', 'active'):
            return
        if event.get('event') == 'video':
            self.video_seen = True
        if event.get('event') == 'status' and event.get('state') == 'connected':
            self.connected = True
        if self.connected and self.video_seen and self.state != 'active':
            self.state = 'active'
            self.deadline = None
            self.stable_at = self.clock() + self.stable_seconds

    def failed(self, permanent=False):
        # Repeated errors from one failed attempt must not postpone its retry.
        if self.state in ('backoff', 'blocked', 'paused'):
            return
        if permanent or not self.enabled or self.attempts >= self.retry_limit:
            self.state, self.deadline = 'blocked', None
        else:
            delay = min(self.retry_max, self.retry_initial * (2 ** self.attempts))
            self.state, self.deadline = 'backoff', self.clock() + delay
        self.stable_at = None

    def tick(self):
        now = self.clock()
        if self.state == 'active' and self.stable_at is not None and now >= self.stable_at:
            self.attempts = 0
            self.stable_at = None
        if self.state == 'starting' and self.deadline is not None and now >= self.deadline:
            self.failed()
        if self.state == 'backoff' and now >= self.deadline:
            self.attempts += 1
            self.begin()
            return True
        return False

    def pause(self):
        self.state, self.deadline, self.stable_at = 'paused', None, None

    def snapshot(self):
        return dict(event='recovery', state=self.state, attempt=self.attempts,
                    limit=self.retry_limit,
                    retry_in=max(0, round(self.deadline - self.clock()))
                    if self.state == 'backoff' else 0)
