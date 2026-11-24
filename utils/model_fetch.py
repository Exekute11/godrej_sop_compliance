"""Best-effort download of the MediaPipe hand-landmarker model."""
import logging
import os
import urllib.request

log = logging.getLogger("sop.models")
URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"


def ensure_hand_model(path: str) -> bool:
    if os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
        return True
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        log.info("Downloading MediaPipe hand model to %s ...", path)
        urllib.request.urlretrieve(URL, path)
        return os.path.getsize(path) > 1_000_000
    except Exception as exc:  # offline plants are common
        log.warning("Hand model download failed (%s). Download manually from %s", exc, URL)
        return False
