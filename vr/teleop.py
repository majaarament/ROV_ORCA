"""
TeleoperationController: head + hand + the research condition -> what the robot should do.

  head  -> where the view points
  hand  -> how far the gripper moves (relative movement, never camera pixels straight to the world)
  shape -> grip, or precision control, depending on the condition

The rear camera is strapped to your head, so every head turn slides your hand across its picture.
To keep the two apart, the hand's direction in the picture is added to the direction the head
pointed when that picture was taken. That sum is where the hand is in the room, and only changes
in it move the gripper: turn your head with your hand still and the gripper stays where it is.
"""
import math
import time
from dataclasses import dataclass, field

import numpy as np

import config
import logger
from tracking import OneEuro


@dataclass
class Controls:
    """One frame of instructions for the robot."""
    yaw: float = 0.0                # head, degrees from straight ahead
    pitch: float = 0.0
    roll: float = 0.0
    head_ok: bool = False
    move: tuple = (0.0, 0.0)        # gripper movement this frame, in view pixels, already scaled
    gesture: str = "OPEN"           # what the gripper does: OPEN / PINCH (gentle) / FIST (firm)
    hand: str = "lost"              # "tracking", "frozen" (just out of sight) or "lost"
    precision: bool = False
    scale: float = 1.0
    side: int = 1
    skeletons: dict = field(default_factory=dict)


class Mapping:
    """The research condition: what the hand's shape means. Swap this to test another interaction."""

    def __init__(self, pinch_action=None, normal=None, precision=None):
        self.pinch_action = pinch_action or config.VR_PINCH_ACTION
        self.normal = config.MOVEMENT_SCALE_NORMAL if normal is None else normal
        self.precision = config.MOVEMENT_SCALE_PRECISION if precision is None else precision

    def apply(self, shape, fine):
        """Hand shape (and whether fine mode was asked for by voice) -> gripper gesture, movement scale."""
        precise = fine
        gesture = shape
        if shape == "PINCH" and self.pinch_action in ("precision", "grip+precision"):
            precise = True
            if self.pinch_action == "precision":
                gesture = "OPEN"            # here a pinch only slows the hand; a fist is the grip
        return gesture, (self.precision if precise else self.normal), precise


class TeleoperationController:
    def __init__(self, head, hand, mapping=None):
        self.head, self.hand = head, hand
        self.mapping = mapping or Mapping()
        self.enabled = True             # off during calibration: the gripper stays put
        self.fine = False               # fine mode asked for by voice or key
        self.view_px = config.VR_EYE_SIZE[0] / math.radians(config.VR_FOV_DEG)   # view pixels per radian
        self._seq = -1
        self._ref = None                # where the hand was in the room at the last detection
        self._held = None               # the same, after the dead zone
        self._filter = None
        self._yaw = self._last_yaw = 0.0
        self._pending = np.zeros(2)     # gripper movement still to be played out between detections
        self._gesture = "OPEN"
        self._state = "lost"
        self._precise = False

    def reset_hand(self):
        """Forget where the hand was: the next detection is a fresh start, not a movement."""
        self._ref = self._filter = self._held = None
        self._pending[:] = 0

    def _in_room(self, anchor, aspect, head):
        """Palm centre in the picture + where the head pointed -> its direction in the room (radians:
        x to the right, y downwards)."""
        yaw, pitch, roll = head
        self._yaw += math.radians((yaw - self._last_yaw + 180) % 360 - 180)   # no jump at +-180
        self._last_yaw = yaw
        half = math.tan(math.radians(config.VR_CAMERA_HFOV_DEG) / 2)
        x, y = (anchor[0] - 0.5) * 2 * half, (anchor[1] - 0.5) * 2 * half / aspect
        r = math.radians(roll)                                  # undo the head's tilt
        x, y = x * math.cos(r) + y * math.sin(r), -x * math.sin(r) + y * math.cos(r)
        return np.array((math.atan(x) + self._yaw, math.atan(y) - math.radians(pitch)))

    def step(self, dt, now=None):
        now = time.time() if now is None else now
        yaw, pitch, roll = self.head.view()
        with self.hand.lock:
            h = self.hand
            seq, visible, gone = h.seq, h.visible, now - h.last_seen
            anchor, aspect, head_then, shape = h.anchor, h.aspect, h.head, h.gesture
            side, skeletons = h.side, h.skeletons

        state = "tracking" if gone < config.HAND_COAST else \
            "frozen" if gone < config.VR_HAND_LOST_WARNING else "lost"
        if state != self._state:
            logger.log("hand", {"tracking": "control_resumed", "frozen": "control_frozen", "lost": "not_visible"}[state])
            self._state = state
        if state != "tracking":
            self.reset_hand()               # freeze: nothing it did while out of sight counts

        if state == "tracking":             # out of sight, the gripper keeps doing what it was doing
            self._gesture = shape
        gesture, scale, precise = self.mapping.apply(self._gesture, self.fine)
        if precise != self._precise:
            self._precise = precise
            logger.log("hand", "precision_on" if precise else "precision_off", f"x{scale:.2f}")

        if seq != self._seq and visible:
            self._seq = seq
            room = self._in_room(anchor, aspect, head_then)
            if self._ref is None:
                # first sight, or back after a gap: take up from here without moving the gripper
                self._filter = OneEuro(*config.SMOOTH_POSITION)
                self._ref = self._held = self._filter(room, now).copy()
            else:
                steady = self._filter(room, now)
                d = steady - self._held             # dead zone: tremor doesn't get through, travel does
                n = float(np.hypot(*d))
                if n > config.VR_HAND_DEAD_ZONE:
                    self._held = self._held + d * (n - config.VR_HAND_DEAD_ZONE) / n
                d = self._held - self._ref
                self._ref = self._held.copy()
                if float(np.hypot(*d)) > config.VR_HAND_MAX_JUMP:
                    self.reset_hand()               # the tracker leapt: start again from wherever it is
                elif self.head.speed < config.VR_HEAD_FAST_DPS and self.enabled:
                    self._pending += d * self.view_px * config.VR_HAND_GAIN * scale

        if not self.enabled:
            self._pending[:] = 0
            gesture = "OPEN"
        k = 1 - (1 - config.VR_HAND_FOLLOW) ** (dt * 60)
        move = self._pending * k
        self._pending -= move
        return Controls(yaw=yaw, pitch=pitch, roll=roll, head_ok=self.head.ok,
                        move=(float(move[0]), float(move[1])), gesture=gesture, hand=state,
                        precision=precise, scale=scale, side=side, skeletons=skeletons)
