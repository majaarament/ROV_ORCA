"""
Webcam tracking (runs in a background thread).

  * Eyes + head  -> where you're looking  -> pans the view
  * Hand         -> where the gripper is
  * Hand shape   -> OPEN (release) / PINCH (gentle grip) / FIST (firm grip)
  * Every finger joint of both hands -> the robot's hands in first-person view

Uses MediaPipe Face Mesh (with iris landmarks) and MediaPipe Hands.
Both models ship inside the mediapipe package, so nothing is downloaded.
"""
import math
import threading
import time

import config

try:
    import cv2
    import mediapipe as mp
    import numpy as np
    TRACKING_LIBS = True
except Exception as e:          # libraries missing -> mouse fallback
    TRACKING_LIBS = False
    _import_error = e

# Face-mesh landmark ids
EYES = [  # (corner a, corner b, upper lid, lower lid)
    (33, 133, 159, 145),
    (362, 263, 386, 374),
]
IRIS_CENTRES = [468, 473]
NOSE, FOREHEAD, CHIN, CHEEK_L, CHEEK_R = 1, 10, 152, 234, 454


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_shape(P, was):
    """OPEN / PINCH / FIST from a hand's 21 joints (x, y pairs). was = the shape it had a moment ago:
    it takes a clearer movement to leave a grip than to make one, so a grip right on the edge doesn't
    flicker and drop the specimen. Distances are in palm lengths, so it works near and far."""
    palm = max(_d(P[0], P[9]), 1e-3)
    pinch = _d(P[4], P[8]) / palm
    curl = sum(_d(P[t], P[0]) for t in (8, 12, 16, 20)) / 4 / palm
    if curl < (config.FIST_RELEASE if was == "FIST" else config.FIST_RATIO):
        return "FIST"
    if pinch < (config.PINCH_RELEASE if was == "PINCH" else config.PINCH_RATIO):
        return "PINCH"
    return "OPEN"


class OneEuro:
    """Smooths a jittery signal: heavily while it is still, hardly at all while it moves fast.
    So a resting hand doesn't tremble and a moving hand doesn't lag. (Casiez et al., 2012)"""

    def __init__(self, min_cutoff, beta):
        self.min_cutoff, self.beta = min_cutoff, beta      # Hz at rest; how quickly speed opens it up
        self.x = self.dx = self.t = self.raw = None

    def __call__(self, x, t):
        x = np.asarray(x, dtype="float64")
        if self.x is None:
            self.x, self.raw, self.dx, self.t = x, x, np.zeros_like(x), t
            return x
        dt = max(t - self.t, 1e-3)
        self.t = t
        alpha = lambda cutoff: 1 / (1 + 1 / (2 * math.pi * cutoff * dt))
        # speed is measured on the raw signal, so it is the hand's real speed (used for coasting too)
        self.dx += alpha(1.0) * ((x - self.raw) / dt - self.dx)
        self.raw = x
        self.x = self.x + alpha(self.min_cutoff + self.beta * np.abs(self.dx)) * (x - self.x)
        return self.x


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.available = False          # camera + libraries working
        self.error = ""
        self.running = True

        # outputs (read by the game)
        self.face_ok = False
        self.gaze = (0.0, 0.0)          # -1..1, smoothed, after calibration
        self.hand_ok = False
        self.hand = (0.5, 0.5)          # 0..1 screen space
        self.gesture = "OPEN"           # OPEN / PINCH / FIST
        self.hand_side = 1              # which of your hands drives the gripper: -1 left, +1 right
        # every hand in view, for drawing: side -> dict(pos = 0..1 screen space, palm = its size in
        # image heights, pts = 21 joints in palm lengths measured from the middle knuckle)
        self.skeletons = {}
        self.preview = None             # (bytes, w, h) RGB thumbnail
        self.fps = 0.0

        self._raw_gaze = (0.0, 0.0)
        self._centre = None
        self._calib_samples = []
        self._calibrating = False
        self._face_since = None
        self._gesture_candidate = "OPEN"
        self._gesture_count = 0
        self._lone_side = 1
        self._side_votes = 0
        self._filters = {}              # side -> smoothing for that hand's position, joints and size
        self._seen = {}                 # side -> (when it was last really seen, its skeleton)

    # ---------- public ----------
    def start(self):
        if not TRACKING_LIBS:
            self.error = f"tracking libraries missing ({_import_error})"
            return
        threading.Thread(target=self._run, daemon=True).start()

    def calibrate(self):
        """Look at the middle of the screen, then call this."""
        with self.lock:
            self._calib_samples = []
            self._calibrating = True

    def is_calibrated(self):
        return self._centre is not None

    def stop(self):
        self.running = False

    # ---------- worker ----------
    def _run(self):
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_SIZE[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_SIZE[1])
        ok, _ = cap.read()
        if not ok:
            self.error = ("can't open the webcam - check System Settings > "
                          "Privacy > Camera for VS Code / Terminal")
            cap.release()
            return

        face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True,
            min_detection_confidence=0.5, min_tracking_confidence=0.5)
        hands = mp.solutions.hands.Hands(
            max_num_hands=2, model_complexity=0,
            min_detection_confidence=0.6, min_tracking_confidence=0.5)
        self.available = True

        last = time.time()
        while self.running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            frame = cv2.flip(frame, 1)                 # mirror, like a selfie
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            rgb.flags.writeable = False
            face_res = face_mesh.process(rgb)
            hand_res = hands.process(rgb)
            rgb.flags.writeable = True

            thumb = cv2.resize(rgb, (224, 168))
            sx, sy = 224 / w, 168 / h

            self._update_face(face_res, w, h, thumb, sx, sy)
            self._update_hand(hand_res, w, h, thumb, sx, sy)

            with self.lock:
                self.preview = (thumb.tobytes(), 224, 168)
            now = time.time()
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(now - last, 1e-3))
            last = now

        cap.release()

    def _update_face(self, res, w, h, thumb, sx, sy):
        if not res.multi_face_landmarks:
            with self.lock:
                self.face_ok = False
            self._face_since = None
            return
        lm = res.multi_face_landmarks[0].landmark
        P = lambda i: (lm[i].x * w, lm[i].y * h)

        # --- eyes: where is each iris inside its eye opening? ---
        ratios_x, ratios_y = [], []
        for iris_id in IRIS_CENTRES:
            iris = P(iris_id)
            # pair the iris with whichever eye it's actually in
            eye = min(EYES, key=lambda e: _d(iris, ((P(e[0])[0] + P(e[1])[0]) / 2,
                                                   (P(e[0])[1] + P(e[1])[1]) / 2)))
            a, b, top, bot = (P(i) for i in eye)
            left, right = min(a[0], b[0]), max(a[0], b[0])
            if right - left < 1:
                continue
            ratios_x.append((iris[0] - left) / (right - left))
            ratios_y.append((iris[1] - top[1]) / max(bot[1] - top[1], 1))
            cv2.circle(thumb, (int(iris[0] * sx), int(iris[1] * sy)), 2, (0, 255, 200), -1)
        if not ratios_x:
            return
        eye_x = sum(ratios_x) / len(ratios_x) - 0.5
        eye_y = sum(ratios_y) / len(ratios_y) - 0.5

        # --- head: where is the nose pointing? ---
        nose, fore, chin = P(NOSE), P(FOREHEAD), P(CHIN)
        cl, cr = P(CHEEK_L), P(CHEEK_R)
        face_w = max(_d(cl, cr), 1)
        face_h = max(_d(fore, chin), 1)
        yaw = (nose[0] - (cl[0] + cr[0]) / 2) / face_w
        pitch = (nose[1] - (fore[1] + chin[1]) / 2) / face_h

        raw = (eye_x * 0.9 + yaw * 1.6, eye_y * 0.5 + pitch * 1.6)

        with self.lock:
            self.face_ok = True
            self._raw_gaze = raw
            if self._face_since is None:
                self._face_since = time.time()
            # auto-calibrate the first time a face is steady for 1.5 s
            if self._centre is None and not self._calibrating and \
                    time.time() - self._face_since > 1.5:
                self._calibrating = True
                self._calib_samples = []
            if self._calibrating:
                self._calib_samples.append(raw)
                if len(self._calib_samples) >= 20:
                    n = len(self._calib_samples)
                    self._centre = (sum(s[0] for s in self._calib_samples) / n,
                                    sum(s[1] for s in self._calib_samples) / n)
                    self._calibrating = False
            if self._centre is not None:
                gx = max(-1, min(1, (raw[0] - self._centre[0]) * config.GAZE_GAIN_X))
                gy = max(-1, min(1, (raw[1] - self._centre[1]) * config.GAZE_GAIN_Y))
                k = config.GAZE_SMOOTHING
                self.gaze = (self.gaze[0] + (gx - self.gaze[0]) * k,
                             self.gaze[1] + (gy - self.gaze[1]) * k)

        cv2.circle(thumb, (int(nose[0] * sx), int(nose[1] * sy)), 3, (255, 200, 0), -1)

    def _update_hand(self, res, w, h, thumb, sx, sy, now=None):
        now = time.time() if now is None else now
        found = sorted(zip(res.multi_hand_landmarks or [], res.multi_handedness or []),
                       key=lambda f: f[0].landmark[9].x)[:2]
        if len(found) == 2:
            sides = [-1, 1]                 # two hands: left and right as they appear on screen
        elif found:
            # one hand: take MediaPipe's left/right guess (the frame is mirrored, so its labels are
            # your real hands), but only once it has said so for a few frames in a row
            label = 1 if found[0][1].classification[0].label == "Right" else -1
            self._side_votes = self._side_votes + 1 if label != self._lone_side else 0
            if self._side_votes >= 6:
                self._lone_side, self._side_votes = label, 0
            sides = [self._lone_side]
        else:
            sides = []

        x0, y0, x1, y1 = config.HAND_BOX
        skeletons, marks = {}, {}
        for (hand, _), side in zip(found, sides):
            lm = hand.landmark
            pts = [(q.x * w / h, q.y) for q in lm]      # image heights, so x and y share a scale
            # palm length; the knuckle row keeps it right when the hand tilts towards the camera
            size = max(_d(pts[0], pts[9]), _d(pts[5], pts[17]) / 0.73, 1e-3)
            # hand position = middle knuckle, mapped from a box in the camera image
            pos = ((lm[9].x - x0) / (x1 - x0), (lm[9].y - y0) / (y1 - y0))
            rel = [((x - pts[9][0]) / size, (y - pts[9][1]) / size) for x, y in pts]
            f = self._filters.setdefault(side, dict(pos=OneEuro(*config.SMOOTH_POSITION),
                                                    pts=OneEuro(*config.SMOOTH_FINGERS),
                                                    palm=OneEuro(1.0, 0.0)))
            pos = f["pos"](pos, now)
            skeletons[side] = dict(pos=(min(1, max(0, pos[0])), min(1, max(0, pos[1]))),
                                   palm=float(f["palm"]([size], now)[0]),
                                   pts=[tuple(q) for q in f["pts"](rel, now)])
            self._seen[side] = (now, skeletons[side], f["pos"].dx.copy())
            marks[side] = lm

        # a hand that vanished a moment ago (blur, a dropped frame) keeps its pose and carries on
        # moving the way it was going for a tenth of a second, instead of blinking out
        for side, (when, last, vel) in list(self._seen.items()):
            if side in skeletons:
                continue
            gone = now - when
            if gone > config.HAND_COAST:
                del self._seen[side]
                self._filters.pop(side, None)       # start fresh when it comes back
                continue
            drift = min(gone, 0.1)
            skeletons[side] = dict(last, pos=(min(1, max(0, last["pos"][0] + vel[0] * drift)),
                                              min(1, max(0, last["pos"][1] + vel[1] * drift))))

        if not skeletons:
            with self.lock:
                self.hand_ok = False
                self.skeletons = {}
            return
        # the hand that was driving the gripper keeps driving it
        side = self.hand_side if self.hand_side in skeletons else next(iter(skeletons))

        if side in marks:                   # only a hand that is really in view can change the gesture
            g = hand_shape([(q.x, q.y) for q in marks[side]], self._gesture_candidate)
            if g == self._gesture_candidate:
                self._gesture_count += 1
            else:
                self._gesture_candidate, self._gesture_count = g, 1

        with self.lock:
            self.hand_ok = True
            self.hand_side = side
            self.hand = skeletons[side]["pos"]
            self.skeletons = skeletons
            if self._gesture_count >= config.GESTURE_FRAMES:
                self.gesture = self._gesture_candidate

        colour = {"OPEN": (200, 200, 200), "PINCH": (0, 220, 255), "FIST": (255, 150, 40)}[self.gesture]
        for s, lm in marks.items():
            for a, b in mp.solutions.hands.HAND_CONNECTIONS:
                pa = (int(lm[a].x * w * sx), int(lm[a].y * h * sy))
                pb = (int(lm[b].x * w * sx), int(lm[b].y * h * sy))
                cv2.line(thumb, pa, pb, colour if s == side else (110, 120, 130), 1)
