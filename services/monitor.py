"""Per-camera processing pipeline (thread) and the multi-camera manager."""
import datetime
import logging
import os
import threading
import time

import cv2


from models import records
from services.detectors import (
    ComponentDetector,
    build_hand_detector,
    zones_for_hands,
    HAND_CONNECTIONS,
    FINGERTIPS,
)
from services.sop_engine import SopEngine
from services.sources import CaptureSource, SimulatorSource
from services.serial_service import SerialService

log = logging.getLogger("sop.monitor")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(ROOT, "data", "screenshots")

BGR_MAGENTA = (94, 16, 185)
BGR_DARK = (72, 12, 143)
BGR_WHITE = (255, 255, 255)
BGR_RED = (40, 40, 225)


class Monitor(threading.Thread):
    """One camera pipeline with independent display and AI processing."""

    def __init__(self, cam_cfg, cfg_all):
        super().__init__(daemon=True, name=f"monitor-{cam_cfg['id']}")

        self.cam_id = cam_cfg["id"]
        self.cam = cam_cfg
        self.rois = cam_cfg["rois"]
        self.det_cfg = cfg_all["detector"]
        self.alerts_cfg = cfg_all["alerts"]

        self._stop_evt = threading.Event()

        # MJPEG output synchronization.
        self._cond = threading.Condition()
        self._frame_id = 0
        self._jpeg = None

        # Camera/display status.
        self.fps = 0.0
        self.aspect = 16 / 9
        self.online = False

        # Screenshot state.
        self._shot_lock = threading.Lock()
        self._last_shot = (0.0, None)

        # AI input: only the newest frame is retained.
        self._ai_cond = threading.Condition()
        self._ai_frame = None
        self._ai_frame_id = 0
        self._ai_processed_id = -1
        self._ai_thread = None

        # AI output: display reads the newest completed result without waiting.
        self._ai_result_lock = threading.Lock()
        self._cached_hands = []
        self._cached_dets = []
        self._cached_comps = None
        # One serial service for the currently active camera/video source.
        self.serial_service = SerialService(
            cfg_all.get("serial", {})
        )
        self.serial_service = SerialService(
            cfg_all.get("serial", {})
)
        self.engine = SopEngine(
            self.cam_id,
            cfg_all["sop"],
            cfg_all["alerts"],
            self._on_event,
            self._on_cycle,
            on_result=self._on_serial_result,
        )

        self.source = self._make_source()
        self.hand_det = build_hand_detector(
            self.det_cfg.get("backend"),
            self.det_cfg.get("hand_model_path", ""),
            self.source.is_simulator,
        )
        self.comp_det = ComponentDetector(
            self.det_cfg.get("component_weights", ""),
            self.det_cfg.get("component_conf", 0.4),
        )

    # ---------------------------------------------------------------- setup
    def _make_source(self):
        if str(self.cam.get("source", "")).lower() == "simulator":
            return SimulatorSource(lambda: self.rois)
        return CaptureSource(self.cam["source"])

    def set_rois(self, rois):
        self.rois = rois

    def stop(self):
        self._stop_evt.set()

        # Wake sleeping consumers immediately.
        with self._ai_cond:
            self._ai_cond.notify_all()
        with self._cond:
            self._cond.notify_all()

        self.source.close()

        if (
            self._ai_thread is not None
            and self._ai_thread.is_alive()
            and self._ai_thread is not threading.current_thread()
        ):
            self._ai_thread.join(timeout=2.0)

        self.hand_det.close()
        self.serial_service.close()

    # ---------------------------------------------------------------- engine sinks
    def _on_serial_result(self, result, cycle_no):
        """
        Send one serial value when the SOP cycle finishes.

        PASS -> ASCII 0
        FAIL -> ASCII 1
        """

        if result == "PASS":
            sent = self.serial_service.send_pass()
            value = "0"

        elif result == "FAIL":
            sent = self.serial_service.send_fail()
            value = "1"

        else:
            return

        if sent:
            print(
                f"SERIAL OUTPUT: {value} "
                f"| {result} | Cycle {cycle_no}"
            )
        else:
            print(
                f"SERIAL OUTPUT FAILED: {value} "
                f"| Check COM port and serial settings"
            )
    def _on_event(self, ev):
        eid = records.add_event(
            ev["camera_id"],
            ev["etype"],
            ev["severity"],
            ev["message"],
            ev["cycle_no"],
        )

        if ev["severity"] == "alert" and self.alerts_cfg.get(
            "auto_screenshot", True
        ):
            shot = self.save_screenshot(ev["etype"])
            if shot:
                records.set_event_screenshot(eid, shot)

    def _on_cycle(self, c):
        records.add_cycle(
            c["camera_id"],
            c["cycle_no"],
            c.get("start_wall"),
            c["duration"],
            c["result"],
            c["violations"],
        )

    def save_screenshot(self, tag="manual"):
        with self._shot_lock:
            last_time, last_name = self._last_shot
            if tag != "manual" and time.time() - last_time < 1.5 and last_name:
                return last_name

            with self._cond:
                data = self._jpeg

            if not data:
                return None

            os.makedirs(SHOT_DIR, exist_ok=True)
            name = (
                f"{self.cam_id}_"
                f"{datetime.datetime.now():%Y%m%d_%H%M%S_%f}_"
                f"{tag}.jpg"
            )

            with open(os.path.join(SHOT_DIR, name), "wb") as output:
                output.write(data)

            self._last_shot = (time.time(), name)
            return name

    # ---------------------------------------------------------------- streaming
    def mjpeg(self):
        last = -1

        while not self._stop_evt.is_set():
            with self._cond:
                self._cond.wait_for(
                    lambda: self._frame_id != last or self._stop_evt.is_set(),
                    timeout=2.0,
                )
                data = self._jpeg
                last = self._frame_id

            if data:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
                    b"Pragma: no-cache\r\n\r\n"
                    + data
                    + b"\r\n"
                )

    def status(self):
        return {
            "online": self.online,
            "fps": round(self.fps, 1),
            "hand_backend": self.hand_det.name,
            "component_model": self.comp_det.active,
            "simulator": self.source.is_simulator,
            "scenario": getattr(self.source, "running_scenario", None),
            "aspect": round(self.aspect, 4),
        }

    # ---------------------------------------------------------------- AI worker
    def _ai_loop(self):
        """Run MediaPipe, YOLO and SOP logic without blocking video output."""

        while not self._stop_evt.is_set():
            with self._ai_cond:
                self._ai_cond.wait_for(
                    lambda: (
                        self._ai_frame_id != self._ai_processed_id
                        or self._stop_evt.is_set()
                    ),
                    timeout=1.0,
                )

                if self._stop_evt.is_set():
                    break

                if (
                    self._ai_frame is None
                    or self._ai_frame_id == self._ai_processed_id
                ):
                    continue

                # Copy only the newest camera frame. There is never an AI backlog.
                frame = self._ai_frame.copy()
                frame_id = self._ai_frame_id

            try:
                hands = self.hand_det.detect(frame, self.source)
                zones = zones_for_hands(hands, self.rois)

                dets = []
                comps = None

                if self.comp_det.active:
                    dets = self.comp_det.detect(frame)
                    comps = ComponentDetector.per_zone(dets, self.rois)

                # SOP decisions are made only from a completed, internally
                # consistent AI result.
                self.engine.update(zones, bool(hands), comps)

                with self._ai_result_lock:
                    self._cached_hands = hands
                    self._cached_dets = dets
                    self._cached_comps = comps

            except Exception:
                log.exception("AI processing error on %s", self.cam_id)

            finally:
                self._ai_processed_id = frame_id

    # ---------------------------------------------------------------- display loop
    def run(self):
        target_fps = max(1, int(self.det_cfg.get("target_fps", 30)))
        interval = 1.0 / target_fps
        fails = 0
        fps_ema = None

        self._ai_thread = threading.Thread(
            target=self._ai_loop,
            daemon=True,
            name=f"ai-{self.cam_id}",
        )
        self._ai_thread.start()

        while not self._stop_evt.is_set():
            loop_start = time.perf_counter()

            try:
                if not self.online:
                    if self.source.open():
                        self.online = True
                        fails = 0
                        self.engine._emit(
                            "CAMERA_ONLINE",
                            "info",
                            f"{self.cam['name']} online",
                        )
                    else:
                        time.sleep(2.0)
                        continue

                ok, frame = self.source.read()

                if not ok or frame is None:
                    fails += 1
                    if fails > 30:
                        self.online = False
                        self.source.close()
                        log.error(
                            "[%s] [SOURCE_OFFLINE] %s lost signal",
                            self.cam_id,
                            self.cam["name"],
                        )
                        self.engine._emit(
                            "CAMERA_OFFLINE",
                            "warn",
                            f"{self.cam['name']} lost signal",
                        )
                    time.sleep(0.02)
                    continue

                fails = 0

                # Publish newest raw frame to the AI worker without waiting.
                with self._ai_cond:
                    self._ai_frame = frame
                    self._ai_frame_id += 1
                    self._ai_cond.notify()

                # Take a quick snapshot of the newest completed detections.
                with self._ai_result_lock:
                    hands = list(self._cached_hands)
                    dets = list(self._cached_dets)

                display_frame = self._draw(frame, hands, dets)
                jpg = self._encode(display_frame)

                if jpg is not None:
                    with self._cond:
                        self._jpeg = jpg
                        self._frame_id += 1
                        self._cond.notify_all()

            except Exception:
                log.exception("display pipeline error on %s", self.cam_id)
                time.sleep(0.1)

            elapsed = time.perf_counter() - loop_start
            instant_fps = 1.0 / max(elapsed, 0.001)
            fps_ema = (
                instant_fps
                if fps_ema is None
                else (0.90 * fps_ema + 0.10 * instant_fps)
            )
            self.fps = min(float(target_fps), fps_ema)

            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    @staticmethod
    def _skeleton(img, lms, width, height):
        """Anti-aliased hand skeleton with smooth bones and joints."""
        points = [
            (int(x * width * 16), int(y * height * 16))
            for x, y in lms
        ]
        shift = 4
        thickness = max(2, width // 320)

        for start, end in HAND_CONNECTIONS:
            cv2.line(
                img,
                points[start],
                points[end],
                BGR_DARK,
                thickness + 4,
                cv2.LINE_AA,
                shift,
            )

        for start, end in HAND_CONNECTIONS:
            cv2.line(
                img,
                points[start],
                points[end],
                BGR_WHITE,
                thickness,
                cv2.LINE_AA,
                shift,
            )

        for index, point in enumerate(points):
            radius = (
                thickness + 4
                if index in FINGERTIPS
                else thickness + 2
                if index == 0
                else thickness + 1
            ) * 16

            cv2.circle(
                img,
                point,
                radius + 16,
                BGR_WHITE,
                -1,
                cv2.LINE_AA,
                shift,
            )
            cv2.circle(
                img,
                point,
                radius,
                BGR_MAGENTA,
                -1,
                cv2.LINE_AA,
                shift,
            )

    def _encode(self, img):
        ok, buffer = cv2.imencode(
            ".jpg",
            img,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                int(self.det_cfg.get("jpeg_quality", 80)),
            ],
        )
        return buffer.tobytes() if ok else None

    def _draw(self, frame, hands, dets):
        original_height, original_width = frame.shape[:2]

        # Keep the existing 960-pixel display width for a good balance of
        # clarity and browser encoding speed.
        if original_width > 960:
            frame = cv2.resize(
                frame,
                (960, int(960 * original_height / original_width)),
                interpolation=cv2.INTER_AREA,
            )

        img = frame.copy()
        height, width = img.shape[:2]
        self.aspect = width / height

        snap = self.engine.snapshot()
        next_zone = (
            snap["progress"] + 1
            if snap["state"] not in ("CYCLE_COMPLETED", "CYCLE_FAILED")
            else None
        )

        overlay = img.copy()

        for key, roi in self.rois.items():
            zone = int(key)
            x1 = int(roi["x"] * width)
            y1 = int(roi["y"] * height)
            x2 = int((roi["x"] + roi["w"]) * width)
            y2 = int((roi["y"] + roi["h"]) * height)

            if zone in snap["occupied"]:
                cv2.rectangle(overlay, (x1, y1), (x2, y2), BGR_MAGENTA, -1)

            color = BGR_WHITE if zone == next_zone else (210, 190, 205)
            cv2.rectangle(
                img,
                (x1, y1),
                (x2, y2),
                color,
                3 if zone == next_zone else 1,
            )

            label = f"Z{zone} {roi['name']}"
            text_width = 12 + 9 * len(label)
            label_x = min(max(0, x1), max(0, width - text_width))
            label_y = y1 - 22 if y1 >= 22 else y1

            cv2.rectangle(
                img,
                (label_x, label_y),
                (label_x + text_width, label_y + 22),
                BGR_MAGENTA,
                -1,
            )
            cv2.putText(
                img,
                label,
                (label_x + 6, label_y + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                BGR_WHITE,
                1,
                cv2.LINE_AA,
            )

        img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)

        for hand in hands:
            if "landmarks" in hand:
                self._skeleton(img, hand["landmarks"], width, height)
            else:
                center = (
                    int(hand["center"][0] * width),
                    int(hand["center"][1] * height),
                )
                cv2.circle(img, center, 14, BGR_WHITE, 2, cv2.LINE_AA)
                cv2.circle(img, center, 6, BGR_MAGENTA, -1, cv2.LINE_AA)

        for detection in dets:
            box = detection["bbox"]
            cv2.rectangle(
                img,
                (int(box[0] * width), int(box[1] * height)),
                (int(box[2] * width), int(box[3] * height)),
                BGR_DARK,
                2,
            )
            cv2.putText(
                img,
                f"{detection['label']} {detection['conf']:.2f}",
                (int(box[0] * width), int(box[1] * height) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                BGR_WHITE,
                1,
                cv2.LINE_AA,
            )

        failed = snap["state"] == "CYCLE_FAILED"
        bar_color = BGR_RED if failed else BGR_MAGENTA
        cv2.rectangle(img, (0, height - 34), (width, height), bar_color, -1)

        status_text = (
            snap["banner"]["text"] if failed else snap["stage_label"]
        ).upper()
        cv2.putText(
            img,
            (
                f"{status_text}   |   CYCLE {snap['cycle']['no']}   "
                f"{snap['cycle']['elapsed']:.0f}s"
            ),
            (10, height - 11),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            BGR_WHITE,
            1,
            cv2.LINE_AA,
        )

        cv2.putText(
            img,
            datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            (width - 190, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            BGR_WHITE,
            1,
            cv2.LINE_AA,
        )

        if failed:
            cv2.rectangle(img, (0, 0), (width - 1, height - 1), BGR_RED, 8)

        return img


class CameraManager:
    def __init__(self, cfg_service):
        self.cfg = cfg_service
        self._lock = threading.RLock()
        self.monitors = {}

    def start_all(self):
        """
        Start only one permanent source at application startup.

        Demo videos are started only when selected from the dashboard.
        This prevents multiple MediaPipe pipelines from running together.
        """

        cfg = self.cfg.get()

        permanent_cameras = [
            camera
            for camera in cfg["cameras"]
            if camera.get("enabled", True)
            and camera["id"] != "demo"
        ]

        if permanent_cameras:
            self._start(
                permanent_cameras[0],
                cfg
            )

    def _start(self, camera, cfg):
        with self._lock:
            if camera["id"] in self.monitors:
                self.monitors[camera["id"]].stop()

            monitor = Monitor(camera, cfg)
            self.monitors[camera["id"]] = monitor
            monitor.start()

    def get(self, cam_id):
        return self.monitors.get(cam_id)

    def camera_list(self):
        output = []

        for camera in self.cfg.get()["cameras"]:
            monitor = self.monitors.get(camera["id"])
            output.append(
                {
                    "id": camera["id"],
                    "name": camera["name"],
                    "source": camera["source"],
                    "enabled": camera.get("enabled", True),
                    **(monitor.status() if monitor else {"online": False}),
                }
            )

        return output

    def stop_other_monitors(self, keep_id):
        """
        Stop every processing pipeline except the selected source.

        Monitors are removed under the manager lock and then stopped
        outside the lock to avoid blocking source switching.
        """

        monitors_to_stop = []

        with self._lock:
            for camera_id in list(self.monitors.keys()):
                if camera_id == keep_id:
                    continue

                monitor = self.monitors.pop(
                    camera_id,
                    None
                )

                if monitor:
                    monitors_to_stop.append(monitor)

        for monitor in monitors_to_stop:
            monitor.stop()

        for monitor in monitors_to_stop:
            if (
                monitor.is_alive()
                and monitor is not threading.current_thread()
            ):
                monitor.join(timeout=5.0)

    def switch_demo_video(self, video_path, filename=None):
        """
        Replace the current demo monitor with the selected local video.

        This changes only the running monitor. It does not write the
        selected video into config.json.
        """

        if not os.path.isfile(video_path):
            raise RuntimeError(
                "selected demo video does not exist"
            )

        cfg_all = self.cfg.get()
        demo_camera = self.cfg.camera("demo")

        # Stop webcam and other demo processing before starting this video.
        self.stop_other_monitors("demo")

        if not demo_camera:
            raise RuntimeError(
                "demo camera slot is missing from config.json"
            )

        display_name = os.path.splitext(
            filename or os.path.basename(video_path)
        )[0]

        display_name = display_name.replace("_", " ")
        display_name = display_name.replace("-", " ")
        display_name = " ".join(display_name.split()).title()

        # Create a temporary in-memory copy.
        # ConfigService returns copied data, so config.json is unchanged.
        demo_camera["source"] = video_path
        demo_camera["name"] = f"Demo - {display_name}"
        demo_camera["enabled"] = True

        with self._lock:
            old_monitor = self.monitors.pop(
                "demo",
                None
            )

            if old_monitor:
                old_monitor.stop()

                if (
                    old_monitor.is_alive()
                    and old_monitor is not threading.current_thread()
                ):
                    old_monitor.join(timeout=3.0)

            new_monitor = Monitor(
                demo_camera,
                cfg_all
            )

            self.monitors["demo"] = new_monitor
            new_monitor.start()
    def activate_camera(self, cam_id):
        """
        Stop all other processing and start only the selected
        permanent webcam/CCTV source.
        """

        camera = self.cfg.camera(cam_id)

        if not camera:
            raise RuntimeError(
                "camera configuration not found"
            )

        cfg_all = self.cfg.get()

        self.stop_other_monitors(cam_id)

        with self._lock:
            existing = self.monitors.get(cam_id)

            if existing and existing.is_alive():
                return

            camera["enabled"] = True

            monitor = Monitor(
                camera,
                cfg_all
            )

            self.monitors[cam_id] = monitor
            monitor.start()
    def reload_camera(self, cam_id):
        cfg = self.cfg.get()
        camera = self.cfg.camera(cam_id)

        with self._lock:
            old = self.monitors.pop(cam_id, None)
            if old:
                old.stop()

            if camera and camera.get("enabled", True):
                self._start(camera, cfg)

    def remove_camera(self, cam_id):
        with self._lock:
            monitor = self.monitors.pop(cam_id, None)
            if monitor:
                monitor.stop()

    def apply_settings(self):
        cfg = self.cfg.get()
        for monitor in self.monitors.values():
            monitor.engine.configure(cfg["sop"], cfg["alerts"])
            monitor.alerts_cfg = cfg["alerts"]

    def update_rois(self, cam_id, rois):
        self.cfg.set_rois(cam_id, rois)
        monitor = self.monitors.get(cam_id)
        if monitor:
            monitor.set_rois(self.cfg.camera(cam_id)["rois"])

    def reset_shift(self, user):
        summary = {"by_camera": {}}

        for camera_id, monitor in self.monitors.items():
            summary["by_camera"][camera_id] = monitor.engine.snapshot()[
                "counters"
            ]

        summary["db"] = records.shift_stats()
        shift = records.reset_shift(user, summary)

        for monitor in self.monitors.values():
            monitor.engine.reset_shift_counters()
            monitor.engine._emit(
                "SHIFT_RESET",
                "info",
                f"Shift reset by {user}",
            )

        return shift

    def stop_all(self):
        for monitor in list(self.monitors.values()):
            monitor.stop()
