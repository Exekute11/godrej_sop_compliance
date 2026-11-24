"""Frame sources: OpenCV capture (USB / RTSP / file) and a built-in simulator for demos and tests."""
import logging
import os
import random
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("sop.source")

SCENARIOS = {
    "correct": [(1, 1.2), (None, 0.7), (2, 1.2), (None, 0.7), (3, 1.2), (None, 0.7), (4, 1.4), (None, 0.5)],
    "skip_spring": [(1, 1.2), (None, 0.7), (3, 1.2), (None, 0.7), (4, 1.4), (None, 0.5)],
    "skip_plain": [(1, 1.2), (None, 0.7), (2, 1.2), (None, 0.7), (4, 1.4), (None, 0.5)],
    "plain_before_spring": [(1, 1.2), (None, 0.7), (3, 1.4), (None, 0.5)],
    "wrong_sequence": [(1, 1.2), (None, 0.7), (2, 1.2), (None, 0.7), (3, 1.2), (None, 0.7), (2, 1.4), (None, 0.5)],
    "component_dropped": [(1, 1.2), (None, 0.7), (2, 1.2), (None, 0.7), (1, 1.4), (None, 0.5)],
    "station_bypass": [(4, 1.4), (None, 0.5)],
    "assembly_timeout": [(1, 1.2), (None, 0.7), (2, 1.2), (None, 9999)],
    "idle": [(None, 9999)],
}


def parse_source(src):
    s = str(src).strip()
    return int(s) if s.isdigit() else s


class CaptureSource:
    """OpenCV capture with a background reader that always keeps only the NEWEST frame.

    Without this, OpenCV queues old frames whenever processing is slower than the camera,
    which is the main cause of 'laggy' live video."""
    is_simulator = False

    def __init__(self, source):
        self.source = parse_source(source)
        self.cap = None
        self._cond = threading.Condition()
        self._frame, self._seq, self._seen, self._alive = None, 0, 0, False
        self._thread = None

    def open(self):
        self.close()
        is_usb = isinstance(self.source, int)
        cap = None
        if is_usb and os.name == "nt":
            cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)   # much faster/lower latency than default on Windows
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if is_usb:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap = cap
        self._alive = True
        self._thread = threading.Thread(target=self._reader, args=(cap,), daemon=True)
        self._thread.start()
        return True

    def _reader(self, cap):
        is_file = (
            isinstance(self.source, str)
            and not self.source.lower().startswith(("rtsp", "http"))
        )

        if is_file:
            video_fps = float(cap.get(cv2.CAP_PROP_FPS))

            if video_fps <= 1 or video_fps > 120:
                video_fps = 25.0

            frame_interval = 1.0 / video_fps
            next_frame_time = time.perf_counter()
        else:
            video_fps = 0.0
            frame_interval = 0.0
            next_frame_time = 0.0

        while self._alive:
            if is_file:
                # Wait until the correct presentation time.
                # Decoding time is included, instead of adding an extra full delay.
                remaining = next_frame_time - time.perf_counter()

                if remaining > 0:
                    time.sleep(remaining)

            ok, frame = cap.read()

            if not ok and is_file:
                # Restart the demo video cleanly.
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                next_frame_time = time.perf_counter()
                continue

            if not ok:
                with self._cond:
                    self._frame = None
                    self._cond.notify_all()

                time.sleep(0.05)
                continue

            with self._cond:
                self._frame = frame
                self._seq += 1
                self._cond.notify_all()

            if is_file:
                next_frame_time += frame_interval

                # If decoding or AI processing falls far behind,
                # resynchronise instead of gradually accumulating delay.
                now = time.perf_counter()

                if now - next_frame_time > 0.25:
                    next_frame_time = now

    def read(self):
        with self._cond:
            self._cond.wait_for(lambda: self._seq != self._seen or not self._alive, timeout=1.0)
            if self._frame is None or self._seq == self._seen:
                return False, None
            self._seen = self._seq
            return True, self._frame

    def close(self):
        self._alive = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class SimulatorSource:
    """Draws a synthetic overhead view with a moving 'hand' so the whole pipeline can be demonstrated offline."""
    is_simulator = True
    W, H = 1280, 720

    def __init__(self, rois_getter):
        self._rois = rois_getter
        self._lock = threading.Lock()
        self._target = None           # zone id or None
        self._pos = None
        self._runner = None
        self._stop = threading.Event()
        self.running_scenario = None

    def open(self):
        return True

    def close(self):
        self._stop.set()

    # ---- control
    def go(self, zone):
        with self._lock:
            self._target = zone
            if zone is None:
                self._pos = None
            else:
                r = self._rois().get(str(zone))
                if r:
                    self._pos = (r["x"] + r["w"] / 2 + random.uniform(-0.03, 0.03),
                                 r["y"] + r["h"] / 2 + random.uniform(-0.04, 0.04))

    def run_scenario(self, name):
        steps = SCENARIOS.get(name)
        if steps is None:
            raise KeyError(name)
        self.stop_scenario()
        self._stop = threading.Event()
        stop = self._stop

        def run():
            self.running_scenario = name
            for zone, dur in steps:
                if stop.is_set():
                    break
                self.go(zone)
                end = time.time() + dur
                while time.time() < end and not stop.is_set():
                    time.sleep(0.05)
            if not stop.is_set():        # finished naturally (a newer run must not be clobbered)
                self.go(None)
                self.running_scenario = None

        self._runner = threading.Thread(target=run, daemon=True)
        self._runner.start()

    def stop_scenario(self):
        self._stop.set()
        self.running_scenario = None
        self.go(None)

    def hand_position(self):
        with self._lock:
            return self._pos

    # ---- frames
    def read(self):
        time.sleep(0.02)
        f = np.full((self.H, self.W, 3), (52, 40, 36), np.uint8)
        for y in range(0, self.H, 40):
            cv2.line(f, (0, y), (self.W, y), (60, 48, 44), 1)
        for x in range(0, self.W, 40):
            cv2.line(f, (x, 0), (x, self.H), (60, 48, 44), 1)
        for k, r in self._rois().items():
            x1, y1 = int(r["x"] * self.W), int(r["y"] * self.H)
            x2, y2 = int((r["x"] + r["w"]) * self.W), int((r["y"] + r["h"]) * self.H)
            cv2.rectangle(f, (x1, y1), (x2, y2), (84, 74, 70), -1)
            cv2.rectangle(f, (x1 + 8, y1 + 8), (x2 - 8, y2 - 8), (110, 98, 92), 2)
        pos = self.hand_position()
        if pos:
            c = (int(pos[0] * self.W), int(pos[1] * self.H))
            cv2.circle(f, c, 44, (120, 160, 215), -1)
            cv2.circle(f, c, 44, (80, 120, 180), 3)
        cv2.putText(f, "SIMULATOR", (self.W - 190, self.H - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 140, 135), 2)
        return True, f
