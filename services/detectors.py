"""Perception layer: hand detection (MediaPipe / skin fallback / simulator) and optional YOLO components.

A hand is a dict: {"center": (x, y), "tips": [(x, y), ...], "bbox": (x1, y1, x2, y2), "conf": float}
with all coordinates normalised to 0..1.
"""
import logging
import os
import threading
import time

import cv2
import numpy as np

from utils.model_fetch import ensure_hand_model

log = logging.getLogger("sop.detect")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#Most important login (Kontahi dataset use nahi kelay , only logic based to save time)
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
                    (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
FINGERTIPS = (4, 8, 12, 16, 20)
# Minimum distance between fingertip data points indicates that a pickup operation has been performed by the operator.
# Each operation zone is fixed and predefined. Any changes to the tray position are considered the operator's responsibility.
# Therefore, the assigned zone should remain unchanged to ensure accurate operation tracking.

class OneEuro:
    """One-Euro filter on a numpy vector: heavy smoothing when still, low lag when moving fast."""

    def __init__(self, freq=15.0, min_cutoff=1.8, beta=25.0, d_cutoff=1.0):
        self.freq, self.min_cutoff, self.beta, self.d_cutoff = freq, min_cutoff, beta, d_cutoff
        self.x = self.dx = None

    @staticmethod
    def _alpha(cutoff, freq):
        tau = 1.0 / (2 * np.pi * cutoff)
        return 1.0 / (1.0 + tau * freq)

    def __call__(self, v, t=None):
        v = np.asarray(v, dtype=np.float32)
        t = time.monotonic() if t is None else t
        if self.x is None:
            self.x, self.dx, self._t = v, np.zeros_like(v), t
            return v
        self.freq = min(60.0, max(5.0, 1.0 / max(t - self._t, 1e-3)))   # follow the real frame rate
        self._t = t
        d = (v - self.x) * self.freq
        ad = self._alpha(self.d_cutoff, self.freq)
        self.dx = ad * d + (1 - ad) * self.dx
        speed = float(np.linalg.norm(self.dx) / max(1, v.shape[0]) ** 0.5)
        a = self._alpha(self.min_cutoff + self.beta * speed, self.freq)
        self.x = a * v + (1 - a) * self.x
        return self.x


class HandSmoother:
    """Tracks hands frame-to-frame (nearest wrist) and smooths their 21 landmarks."""

    def __init__(self, max_missing=4):
        self.tracks, self.max_missing = [], max_missing

    def apply(self, hands):
        used, out = set(), []
        for h in hands:
            w = np.array(h["landmarks"][0])
            best, bd = None, 0.25
            for i, t in enumerate(self.tracks):
                if i in used:
                    continue
                d = float(np.linalg.norm(t["wrist"] - w))
                if d < bd:
                    best, bd = i, d
            if best is None:
                self.tracks.append({"f": OneEuro(), "wrist": w, "missing": 0})
                best = len(self.tracks) - 1
            used.add(best)
            t = self.tracks[best]
            t["missing"], t["wrist"] = 0, w
            pts = t["f"](np.array(h["landmarks"], dtype=np.float32))
            h["landmarks"] = [(float(x), float(y)) for x, y in pts]
            palm = pts[[0, 5, 9, 13, 17]].mean(axis=0)
            h["center"] = (float(palm[0]), float(palm[1]))
            h["tips"] = [h["landmarks"][i] for i in FINGERTIPS]
            xs, ys = pts[:, 0], pts[:, 1]
            h["bbox"] = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
            out.append(h)
        for i, t in enumerate(self.tracks):
            if i not in used:
                t["missing"] += 1
        self.tracks = [t for t in self.tracks if t["missing"] <= self.max_missing]
        return out


def synthetic_landmarks(cx, cy, s=0.075):
    """A fixed open-hand template (21 points) used by the simulator so the skeleton can be demoed offline."""
    t = [(0, 0.9), (-.55, .6), (-.9, .25), (-1.15, -.05), (-1.35, -.3), (-.45, -.15), (-.5, -.75), (-.52, -1.1), (-.53, -1.4),
         (-.05, -.2), (-.05, -.9), (-.05, -1.3), (-.05, -1.65), (.38, -.12), (.42, -.75), (.44, -1.1), (.45, -1.4),
         (.75, .0), (.85, -.5), (.9, -.8), (.95, -1.05)]
    return [(cx + x * s * 0.6, cy + y * s) for x, y in t]


class BaseHandDetector:
    name = "base"

    def detect(self, frame, source=None):
        return []

    def close(self):
        pass


class SimulatorHandDetector(BaseHandDetector):
    name = "simulator"

    def detect(self, frame, source=None):
        pos = source.hand_position() if source is not None and hasattr(source, "hand_position") else None
        if not pos:
            return []
        x, y = pos
        lm = synthetic_landmarks(x, y)
        h = {"center": (x, y), "tips": [lm[i] for i in FINGERTIPS], "landmarks": lm, "conf": 1.0}
        xs, ys = [p[0] for p in lm], [p[1] for p in lm]
        h["bbox"] = (min(xs), min(ys), max(xs), max(ys))
        return [h]


class MediaPipeHandDetector(BaseHandDetector):
    name = "mediapipe"

    def __init__(self, model_path, max_hands=2):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self._mp = mp
        opts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO, num_hands=max_hands,
            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5)
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._ts = 0
        self._smoother = HandSmoother()

    def detect(self, frame, source=None):
        h0, w0 = frame.shape[:2]
        if w0 > 512:
            frame = cv2.resize(
                frame,
                (512, int(512 * h0 / w0)),
                interpolation=cv2.INTER_AREA
            )

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        self._ts = max(self._ts + 1, int(time.monotonic() * 1000))   # real, strictly increasing timestamps
        res = self._lm.detect_for_video(img, self._ts)
        hands = []
        for lms in res.hand_landmarks or []:
            pts = [(l.x, l.y) for l in lms]
            palm = [pts[i] for i in (0, 5, 9, 13, 17)]
            cx = sum(p[0] for p in palm) / 5
            cy = sum(p[1] for p in palm) / 5
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            hands.append({"center": (cx, cy), "tips": [pts[i] for i in (4, 8, 12, 16, 20)], "landmarks": pts,
                          "bbox": (min(xs), min(ys), max(xs), max(ys)), "conf": 0.9})
        return self._smoother.apply(hands)

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass


class SkinHandDetector(BaseHandDetector):
    """Colour-based fallback when MediaPipe is unavailable. Less accurate; use controlled lighting/gloves off."""
    name = "skin-fallback"

    def __init__(self):
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    def detect(self, frame, source=None):
        h0, w0 = frame.shape[:2]
        small = cv2.resize(frame, (480, int(480 * h0 / w0)))
        ycc = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycc, (0, 135, 85), (255, 180, 135))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel, iterations=2)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        sh, sw = small.shape[:2]
        hands = []
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:2]:
            if cv2.contourArea(c) < 0.004 * sw * sh:
                continue
            x, y, w, h = cv2.boundingRect(c)
            m = cv2.moments(c)
            cx, cy = (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] else (x + w / 2, y + h / 2)
            hands.append({"center": (cx / sw, cy / sh), "tips": [], "bbox": (x / sw, y / sh, (x + w) / sw, (y + h) / sh),
                          "conf": 0.5})
        return hands


def build_hand_detector(backend, model_path, is_simulator):
    backend = (backend or "auto").lower()
    if is_simulator and backend in ("auto", "simulator"):
        return SimulatorHandDetector()
    if backend in ("auto", "mediapipe"):
        path = model_path if os.path.isabs(model_path) else os.path.join(ROOT, model_path)
        if ensure_hand_model(path):
            try:
                return MediaPipeHandDetector(path)
            except Exception as exc:
                log.warning("MediaPipe unavailable (%s); falling back to skin detector", exc)
        else:
            log.warning("MediaPipe model missing; falling back to skin detector")
    return SkinHandDetector()


class ComponentDetector:
    """Optional YOLO detector for bolt / spring_washer / plain_washer. Inactive when weights are absent."""

    def __init__(self, weights, conf=0.4, every_n=3):
        self.active, self.conf, self.every_n, self._n, self._last = False, conf, every_n, 0, []
        self._lock = threading.Lock()
        path = weights if os.path.isabs(weights) else os.path.join(ROOT, weights)
        if os.path.isfile(path):
            try:
                from ultralytics import YOLO
                self.model = YOLO(path)
                self.active = True
                log.info("Component model loaded: %s", path)
            except Exception as exc:
                log.warning("Component model not usable (%s)", exc)

    def detect(self, frame):
        if not self.active:
            return []
        self._n += 1
        if self._n % self.every_n:
            return self._last
        h, w = frame.shape[:2]
        out = []
        with self._lock:
            res = self.model.predict(frame, conf=self.conf, verbose=False)[0]
        for b in res.boxes:
            name = res.names[int(b.cls)].lower()
            label = "bolt" if "bolt" in name else "spring_washer" if ("spring" in name or "split" in name) else \
                "plain_washer" if ("plain" in name or "washer" in name) else name
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            out.append({"label": label, "conf": float(b.conf), "center": ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h),
                        "bbox": (x1 / w, y1 / h, x2 / w, y2 / h)})
        self._last = out
        return out

    @staticmethod
    def per_zone(dets, rois):
        zones = {1: set(), 2: set(), 3: set(), 4: set()}
        for d in dets:
            for k, r in rois.items():
                if _inside(d["center"], r):
                    zones[int(k)].add(d["label"])
        return zones


def _inside(pt, r):
    return r["x"] <= pt[0] <= r["x"] + r["w"] and r["y"] <= pt[1] <= r["y"] + r["h"]


def zones_for_hands(hands, rois):
    occupied = set()
    for h in hands:
        for k, r in rois.items():
            tips_in = sum(1 for t in h.get("tips", []) if _inside(t, r))
            if _inside(h["center"], r) or tips_in >= 3:
                occupied.add(int(k))
    return occupied
