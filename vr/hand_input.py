"""
HandInput: your hand, as the phone's rear camera sees it.

MediaPipe runs on the phone and sends only the 21 joints of each hand, never the picture.
This turns them into the same things the webcam tracker gives the game (which hand, its joints
for the robot's hand, OPEN / PINCH / FIST) plus a steady anchor at the centre of the palm, which
vr/teleop.py turns into gripper movement.
"""
import threading
import time

import config
import logger
from tracking import OneEuro, _d, hand_shape

PALM = (0, 5, 9, 13, 17)            # wrist and the four knuckles: their centre barely moves when fingers do
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class HandInput:
    def __init__(self):
        self.lock = threading.Lock()
        self.available = None           # None = not known yet, False = no camera or no tracker on the phone
        self.error = ""
        self.visible = False            # a hand in the latest camera frame
        self.last_seen = 0.0
        self.seq = 0                    # goes up with every detection that had a hand in it
        # the hand that drives the gripper, in the latest detection
        self.anchor = (0.5, 0.5)        # palm centre in the camera image, 0..1
        self.palm = 0.2                 # palm length in image heights: bigger = closer to the camera
        self.aspect = 4 / 3             # camera image width / height
        self.head = (0.0, 0.0, 0.0)     # where the head pointed when that frame was taken
        self.gesture = "OPEN"           # the hand's shape: OPEN / PINCH / FIST
        self.side = 1                   # -1 left hand, +1 right hand
        self.skeletons = {}             # side -> joints, for drawing the robot's hands
        self.marks = {}                 # side -> raw joints in the image, for the debug preview
        self.fps = 0.0                  # detections per second on the phone

        self._candidate, self._count = "OPEN", 0
        self._lone_side, self._side_votes = 1, 0
        self._filters = {}
        self._logged_visible = False

    # ---------- from the phone ----------
    def push(self, msg, now=None):
        """One detection: msg["hands"] = [{label, lm: [x0, y0, x1, y1, ...]}], empty if no hand."""
        now = time.time() if now is None else now
        hands = []
        for h in msg.get("hands", [])[:2]:
            lm = h.get("lm", [])
            if len(lm) >= 42:
                hands.append((h.get("label", "Right"), [(lm[i], lm[i + 1]) for i in range(0, 42, 2)]))
        hands.sort(key=lambda h: h[1][9][0])
        aspect = float(msg.get("a") or 4 / 3)

        if len(hands) == 2:
            sides = [-1, 1]                 # the camera looks where you look: left in the picture is your left
        elif hands:
            # one hand: take MediaPipe's left/right guess once it has said so for a few frames in a row
            label = 1 if (hands[0][0] == "Right") != config.VR_SWAP_HANDEDNESS else -1
            self._side_votes = self._side_votes + 1 if label != self._lone_side else 0
            if self._side_votes >= 6:
                self._lone_side, self._side_votes = label, 0
            sides = [self._lone_side]
        else:
            sides = []

        x0, y0, x1, y1 = config.HAND_BOX
        skeletons, marks, anchors = {}, {}, {}
        for (_, lm), side in zip(hands, sides):
            pts = [(x * aspect, y) for x, y in lm]          # image heights, so x and y share a scale
            size = max(_d(pts[0], pts[9]), _d(pts[5], pts[17]) / 0.73, 1e-3)
            anchor = (sum(lm[i][0] for i in PALM) / 5, sum(lm[i][1] for i in PALM) / 5)
            rel = [((x - pts[9][0]) / size, (y - pts[9][1]) / size) for x, y in pts]
            f = self._filters.setdefault(side, dict(pts=OneEuro(*config.SMOOTH_FINGERS), palm=OneEuro(1.0, 0.0)))
            palm = float(f["palm"]([size], now)[0])
            skeletons[side] = dict(pos=(min(1, max(0, (anchor[0] - x0) / (x1 - x0))),
                                        min(1, max(0, (anchor[1] - y0) / (y1 - y0)))),
                                   palm=palm, pts=[tuple(q) for q in f["pts"](rel, now)])
            marks[side], anchors[side] = lm, (anchor, palm)

        with self.lock:
            self.available, self.fps, self.aspect = True, float(msg.get("fps") or 0), aspect
            if not skeletons:
                self.visible, self.marks = False, {}
                gone = now - self.last_seen
                if gone > config.VR_HAND_LOST_WARNING:      # until then the robot's hand holds its pose
                    self.skeletons = {}
                    self._filters.clear()
                if gone > config.HAND_COAST and self._logged_visible:
                    self._logged_visible = False
                    logger.log("hand", "lost")
                return
            side = self.side if self.side in skeletons else next(iter(skeletons))
            shape = hand_shape(marks[side], self._candidate)
            if shape == self._candidate:
                self._count += 1
            else:
                self._candidate, self._count = shape, 1
            if self._count >= config.GESTURE_FRAMES and shape != self.gesture:
                if "PINCH" in (shape, self.gesture):
                    logger.log("hand", "pinch_start" if shape == "PINCH" else "pinch_release", f"{self.gesture}->{shape}")
                self.gesture = shape
            self.visible, self.last_seen, self.seq = True, now, self.seq + 1
            self.side, self.skeletons, self.marks = side, skeletons, marks
            self.anchor, self.palm = anchors[side]
            self.head = tuple(msg.get("head") or (0.0, 0.0, 0.0))
            if not self._logged_visible:
                self._logged_visible = True
                logger.log("hand", "detected", "right" if side == 1 else "left")

    # ---------- for the experimenter's screen ----------
    def preview(self, w=224, h=168):
        """The joints as a small picture (bytes, w, h). Drawn from the landmarks: there is no video here."""
        import cv2
        import numpy as np
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (8, 30, 42)
        with self.lock:
            marks, driving, gesture = dict(self.marks), self.side, self.gesture
        colour = {"OPEN": (200, 200, 200), "PINCH": (0, 220, 255), "FIST": (255, 150, 40)}[gesture]
        for side, lm in marks.items():
            for a, b in BONES:
                cv2.line(img, (int(lm[a][0] * w), int(lm[a][1] * h)), (int(lm[b][0] * w), int(lm[b][1] * h)),
                         colour if side == driving else (110, 120, 130), 1)
        return img.tobytes(), w, h
