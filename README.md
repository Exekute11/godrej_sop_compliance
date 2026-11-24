# Godrej AI SOP Compliance Monitoring System

Real-time SOP monitoring for the **Bolt -> Spring Washer -> Plain Washer -> Assembly** workflow using an overhead
camera. Zone + AI hybrid: hand tracking (MediaPipe) decides which station ROI the operator touches, an optional
YOLO model verifies components, and a state machine validates the SOP, raises violations and logs every cycle.

## Quick start (Windows PowerShell)
```powershell
cd godrej_sop_monitor
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000 (Linux/macOS: `source venv/bin/activate`).

The default camera is the built-in **simulator**, so the whole system runs with no hardware. Sign in as
supervisor and press a scenario chip under the feed (correct, skip spring, skip plain, wrong sequence, ...).

| User | Password | Role |
|---|---|---|
| admin | admin123 | admin |
| supervisor | super123 | supervisor |
| operator | operator123 | operator |

**Change these passwords on first login** (Settings -> My Account, or Users page) and set a real secret key:
`$env:SOP_SECRET_KEY = "long-random-string"` (or `app.secret_key` in config.json).

## Connecting a real camera
Settings (admin) -> Cameras: source can be `0` (USB), `rtsp://user:pass@ip/stream`, or a video file path.
Add as many cameras as needed; each runs its own pipeline thread and state machine. Then open **Zones** and
drag the four ROIs over Bolt Bin, Spring Washer Bin, Plain Washer Bin and Assembly Area and press Save.

Hand detection: on first start the MediaPipe hand model downloads to `models/hand_landmarker.task`. On an offline
PC, download it once from
`https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task`
and place it there. If unavailable, the system falls back to a colour-based skin detector (less accurate).

Optional component AI: train a YOLOv8 model with classes containing `bolt`, `spring`/`split`, `plain`, save it as
`models/components.pt` and `pip install ultralytics`. When active, an assembly with no bolt visible in the
Assembly ROI raises **Missing Component**.

## SOP logic (services/sop_engine.py)
A hand must dwell in a zone for `dwell_ms` to count as a pick; the zone must be empty ~0.4 s before it can fire again.
`progress` = completed steps. Entering zone `progress+1` advances; anything else is a violation:

| Situation | Violations raised |
|---|---|
| Bolt -> Plain Washer | Spring skipped, Plain before spring, Wrong sequence |
| Bolt -> Assembly | Spring skipped, Plain skipped, Assembly incomplete, Station bypass |
| Bolt, Spring -> Assembly | Plain skipped, Assembly incomplete |
| Return to an earlier station | Wrong sequence (or Component dropped if Bolt Bin) |
| Start at Spring/Plain/Assembly | Station bypass, Missing component |
| Cycle longer than timeout | Assembly timeout |
| No hand activity > idle threshold | Operator idle |

A failed cycle shows the alert banner for `fail_hold_s`, then resets. A PASS shows for `pass_hold_s`; picking
the next bolt during that time starts the next cycle immediately. Each violation can be switched on/off in Settings.

## Roles
viewer: watch only | operator: + screenshots | supervisor: + zones, thresholds, alert config, shift reset, CSV
exports, supervisor dashboard, audit log | admin: + users/roles, cameras, detector settings.
All state-changing API calls need the session CSRF token; logins lock for 60 s after 5 failures; sensitive
actions are written to the audit log.

## Project layout
```
app.py                 application factory + entry point
config.json            thresholds, alerts, detector, cameras and ROIs (normalised 0-1)
routes/                auth.py, pages.py, api.py (Blueprints, REST)
services/              sop_engine.py (state machine), detectors.py, sources.py (+simulator),
                       monitor.py (per-camera thread + CameraManager), config_service.py
models/                sop.py (labels), records.py (events/cycles/shifts/audit), users.py
database/db.py         SQLite (WAL), thread-safe
utils/                 security (roles, CSRF), csvutil, timeutil, model_fetch
templates/ static/     Godrej-branded UI (css/style.css = your brand file, css/sop.css = additions)
tests/                 pytest: state machine + full API/simulator integration
data/                  sop_monitor.db and violation screenshots (created at runtime)
```

## REST API (selection)
`GET /api/cameras/<id>/state?since=<event_id>` live snapshot + new events | `GET /video_feed/<id>` MJPEG |
`PUT /api/cameras/<id>/rois` | `POST /api/cameras/<id>/screenshot` | `GET|PUT /api/settings` |
`POST /api/shift/reset` | `GET /api/export/{events,cycles,shift,audit}.csv` | `/api/users` (admin) | `/api/audit`.

## Tests
`pip install pytest` then `python -m pytest -q tests` (22 tests, ~15 s).

## Production notes
Run behind a reverse proxy with HTTPS, keep a single process (camera threads live in-process) and back up
`data/sop_monitor.db`. The video overlay marks violations in red; the dashboard UI keeps the Godrej magenta/white
palette from your reference stylesheet, with the alert state shown as a solid, pulsing magenta banner.
