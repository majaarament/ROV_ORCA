"""
HeadInput: where the headset points.

The phone reads its orientation sensors and sends yaw, pitch and roll in degrees (yaw to the
right, pitch up, roll anticlockwise are positive). This keeps the latest reading, the neutral
"straight ahead" direction, and how fast the head is turning.
"""
import math
import threading
import time

import config
import logger


def wrap(deg):
    """Any angle -> -180..180."""
    return (deg + 180) % 360 - 180


class HeadInput:
    def __init__(self):
        self.lock = threading.Lock()
        self.raw = (0.0, 0.0, 0.0)      # as the phone reports it
        self.yaw0 = self.pitch0 = 0.0   # what counts as straight ahead
        self.speed = 0.0                # degrees per second, smoothed
        self.last = 0.0                 # when the last reading arrived
        self.available = None           # None = not known yet, False = this phone has no sensors
        self._ts = None
        self._moving_since = None
        self._calm_since = 0.0
        self._turned = 0.0

    @property
    def ok(self):
        return time.time() - self.last < 0.5

    # ---------- from the phone ----------
    def push(self, yaw, pitch, roll, ts, now=None):
        """ts = the phone's own clock in milliseconds, so network hiccups don't look like speed."""
        now = time.time() if now is None else now
        with self.lock:
            if self._ts is not None and ts > self._ts:
                dt = min((ts - self._ts) / 1000, 0.25)
                turn = math.hypot(wrap(yaw - self.raw[0]), pitch - self.raw[1])
                self.speed += 0.3 * (turn / max(dt, 1e-3) - self.speed)
                self._watch(turn, now)
            self._ts, self.raw, self.last, self.available = ts, (yaw, pitch, roll), now, True

    def _watch(self, turn, now):
        """Log each significant head movement: when it started, how long it took, how far it went."""
        if self.speed > config.VR_HEAD_MOVE_DPS:
            if self._moving_since is None:
                self._moving_since, self._turned = now, 0.0
                logger.log("head", "move_start", f"{self.speed:.0f} deg/s")
            self._calm_since = now
        if self._moving_since is not None:
            self._turned += turn
            if self.speed < config.VR_HEAD_MOVE_DPS / 2 and now - self._calm_since > 0.3:
                logger.log("head", "move_end", f"{self._calm_since - self._moving_since:.2f}s,{self._turned:.0f} deg")
                self._moving_since = None

    # ---------- for the game ----------
    def recenter(self):
        """Wherever the headset points now becomes straight ahead."""
        with self.lock:
            self.yaw0, self.pitch0 = self.raw[0], self.raw[1]
        logger.log("head", "recenter", f"yaw0={self.yaw0:.1f},pitch0={self.pitch0:.1f}")

    def view(self):
        """Yaw, pitch and roll in degrees from straight ahead."""
        with self.lock:
            return wrap(self.raw[0] - self.yaw0), self.raw[1] - self.pitch0, self.raw[2]
