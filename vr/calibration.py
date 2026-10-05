"""
The short sequence before the dive, done without taking the headset off:

  1. look straight ahead      -> that direction becomes "forward"
  2. hold one hand up         -> the rear camera finds it
  3. open your hand           -> confirmed
  4. pinch thumb and index    -> confirmed
  READY, and the dive begins.

Each step moves on by itself once it has seen what it asked for. ORCA says each step aloud.
The experimenter can press ENTER on the Mac to skip a step that will not pass.
"""
import time

import config
import logger

STEPS = ["look", "hand", "open", "pinch", "ready"]
CARDS = {
    "look": ("LOOK STRAIGHT AHEAD", "Hold still for a moment"),
    "hand": ("HOLD ONE HAND IN FRONT OF YOU", "Searching for hand..."),
    "open": ("OPEN YOUR HAND", "Fingers apart"),
    "pinch": ("PINCH YOUR THUMB AND INDEX FINGER", "Touch the tips together"),
    "ready": ("READY", "Begin dive"),
}
HOLD = {"look": 1.5, "hand": 0.5, "open": 0.6, "pinch": 0.25, "ready": 2.5}    # seconds each must be held
TICK = 0.9                                                                   # how long the tick stays up


class Calibration:
    def __init__(self, head, hand, orca):
        self.head, self.hand, self.orca = head, hand, orca
        self.step = None                # None = not running
        self.done = False
        self._since = self._passed = None
        self._started = 0.0

    def begin(self):
        self.done = False
        self._started = time.time()
        logger.log("vr", "calibration_start")
        self._go("look")

    def _go(self, step):
        self.step, self._since, self._passed = step, None, None
        self._entered = time.time()
        if step is not None:
            self.orca.say(self.orca.line("cal_" + step), "calibration")

    def skip(self):
        if self.step is not None:
            logger.log("vr", "calibration_skipped", self.step)
            self._pass(time.time())

    def _pass(self, now):
        if self._passed is None:
            self._passed = now
            logger.log("vr", "calibration_step", f"{self.step},{now - self._entered:.1f}s")
            if self.step == "look":
                self.head.recenter()

    def _met(self):
        """Is the thing this step asks for happening right now?"""
        if self.step == "look":
            # no motion sensors on this phone: nothing to wait for
            return self.head.available is False or (self.head.ok and self.head.speed < 8)
        if self.step == "hand":
            return self.hand.available is False or self.hand.visible
        if self.step in ("open", "pinch"):
            return self.hand.available is False or (self.hand.visible and self.hand.gesture == self.step.upper())
        return True

    def update(self, now=None):
        """Call every frame. True on the frame the calibration finishes."""
        if self.step is None:
            return False
        now = time.time() if now is None else now
        if self._passed is None:
            if self._met():
                self._since = self._since or now
                if now - self._since >= HOLD[self.step]:
                    self._pass(now)
            else:
                self._since = None
        if self._passed is not None and now - self._passed >= (0 if self.step == "ready" else TICK):
            nxt = STEPS.index(self.step) + 1
            if nxt < len(STEPS):
                self._go(STEPS[nxt])
            else:
                self.step, self.done = None, True
                logger.log("vr", "calibration_done", f"{now - self._started:.1f}s")
                return True
        return False

    def card(self):
        """What the headset shows for this step."""
        if self.step is None:
            return None
        title, sub = CARDS[self.step]
        if self._passed is not None and self.step != "ready":
            sub = {"look": "View centred ✓", "hand": "Hand detected ✓",
                   "open": "Open hand ✓", "pinch": "Pinch ✓"}[self.step]
        elif self.step == "hand" and self.hand.available is False:
            sub = "No hand camera on this phone"
        return dict(title=title, sub=sub, ok=self._passed is not None,
                    step=STEPS.index(self.step) + 1, of=len(STEPS) - 1)
