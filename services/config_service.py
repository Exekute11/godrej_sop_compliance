"""Thread-safe JSON configuration with atomic saves."""
import copy
import json
import os
import threading

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

ALL_VIOLATIONS = ["SPRING_SKIPPED", "PLAIN_SKIPPED", "WRONG_SEQUENCE", "PLAIN_BEFORE_SPRING", "ASSEMBLY_INCOMPLETE",
                  "ASSEMBLY_TIMEOUT", "STATION_BYPASS", "MISSING_COMPONENT", "COMPONENT_DROPPED", "OPERATOR_IDLE"]


class ConfigService:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        with open(path, "r", encoding="utf-8") as f:
            self._cfg = json.load(f)

    def get(self):
        with self._lock:
            return copy.deepcopy(self._cfg)

    def section(self, name):
        with self._lock:
            return copy.deepcopy(self._cfg.get(name, {}))

    def camera(self, cam_id):
        with self._lock:
            for c in self._cfg["cameras"]:
                if c["id"] == cam_id:
                    return copy.deepcopy(c)
        return None

    def update_section(self, name, values):
        with self._lock:
            self._cfg.setdefault(name, {}).update(values)
            self._save()

    def set_rois(self, cam_id, rois):
        clean = {}
        for k, r in rois.items():
            if k not in ("1", "2", "3", "4"):
                raise ValueError("ROI ids must be 1..4")
            x, y, w, h = (float(r[i]) for i in ("x", "y", "w", "h"))
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
                raise ValueError(f"ROI {k} is outside the frame")
            clean[k] = {"name": str(r.get("name", f"ROI {k}"))[:40], "x": x, "y": y, "w": w, "h": h}
        with self._lock:
            for c in self._cfg["cameras"]:
                if c["id"] == cam_id:
                    c["rois"].update(clean)
                    self._save()
                    return
        raise KeyError(cam_id)

    def upsert_camera(self, cam):
        with self._lock:
            for i, c in enumerate(self._cfg["cameras"]):
                if c["id"] == cam["id"]:
                    self._cfg["cameras"][i].update({k: v for k, v in cam.items() if k != "rois"})
                    self._save()
                    return
            cam.setdefault("rois", copy.deepcopy(self._cfg["cameras"][0]["rois"]) if self._cfg["cameras"] else {})
            cam.setdefault("enabled", True)
            self._cfg["cameras"].append(cam)
            self._save()

    def delete_camera(self, cam_id):
        with self._lock:
            if len(self._cfg["cameras"]) <= 1:
                raise ValueError("at least one camera is required")
            self._cfg["cameras"] = [c for c in self._cfg["cameras"] if c["id"] != cam_id]
            self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cfg, f, indent=2)
        os.replace(tmp, self.path)
