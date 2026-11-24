"""REST API (Blueprint). All state-changing calls require the X-CSRF-Token header."""
import os
from flask import Blueprint, Response, current_app, g, jsonify, request, send_from_directory, abort

from models import records, users as users_model
from models.sop import EVENT_LABELS, VIOLATION_CODES
from services.monitor import SHOT_DIR
from services.sources import SCENARIOS
from utils.csvutil import csv_response
from utils.security import role_required

ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DEMO_VIDEO_DIR = os.path.join(
    ROOT,
    "data",
    "demo_videos"
)

ALLOWED_DEMO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm"
}

bp = Blueprint("api", __name__)


def mgr():
    return current_app.extensions["manager"]


def cfg():
    return current_app.extensions["cfg"]


def mon_or_404(cam_id):
    m = mgr().get(cam_id)
    if not m:
        abort(404, "camera not found or disabled")
    return m


def audit(action, details=""):
    records.audit(g.user["username"], action, details, request.remote_addr)


def body():
    return request.get_json(silent=True) or {}

def available_demo_videos():
    """
    Return supported video files from data/demo_videos.

    Only filenames are returned. Full filesystem paths are never
    accepted from the browser.
    """

    os.makedirs(DEMO_VIDEO_DIR, exist_ok=True)

    videos = []

    for filename in sorted(
        os.listdir(DEMO_VIDEO_DIR),
        key=str.lower
    ):
        full_path = os.path.join(
            DEMO_VIDEO_DIR,
            filename
        )

        if not os.path.isfile(full_path):
            continue

        extension = os.path.splitext(filename)[1].lower()

        if extension not in ALLOWED_DEMO_EXTENSIONS:
            continue

        display_name = os.path.splitext(filename)[0]
        display_name = display_name.replace("_", " ")
        display_name = display_name.replace("-", " ")
        display_name = " ".join(display_name.split())
        display_name = display_name.title()

        videos.append({
            "filename": filename,
            "name": display_name
        })

    return videos


def labelled(rows):
    for r in rows:
        r["label"] = EVENT_LABELS.get(r["etype"], r["etype"])
    return rows


# ------------------------------------------------------------------ cameras / live state
@bp.get("/api/cameras")
@role_required("viewer")
def cameras():
    return jsonify(cameras=mgr().camera_list())

@bp.post("/api/cameras/<cam_id>/activate")
@role_required("viewer")
def activate_camera(cam_id):
    try:
        mgr().activate_camera(cam_id)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 404

    audit("CAMERA_ACTIVATE", cam_id)

    return jsonify(
        ok=True,
        camera_id=cam_id
    )

@bp.get("/api/demo-videos")
@role_required("viewer")
def demo_videos():
    return jsonify(
        videos=available_demo_videos()
    )

@bp.post("/api/demo-videos/select")
@role_required("viewer")
def select_demo_video():
    request_body = body()
    filename = os.path.basename(
        str(request_body.get("filename", ""))
    )

    if not filename:
        return jsonify(
            error="video filename is required"
        ), 400

    available = {
        video["filename"]
        for video in available_demo_videos()
    }

    if filename not in available:
        return jsonify(
            error="demo video not found or unsupported"
        ), 404

    full_path = os.path.join(
        DEMO_VIDEO_DIR,
        filename
    )

    try:
        mgr().switch_demo_video(
            full_path,
            filename
        )
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 409

    audit(
        "DEMO_VIDEO_SELECT",
        filename
    )

    return jsonify(
        ok=True,
        camera_id="demo",
        filename=filename
    )

@bp.get("/video_feed/<cam_id>")
@role_required("viewer")
def video_feed(cam_id):
    m = mon_or_404(cam_id)
    return Response(m.mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


@bp.get("/api/cameras/<cam_id>/state")
@role_required("viewer")
def state(cam_id):
    m = mon_or_404(cam_id)
    since = request.args.get("since", 0, type=int)
    snap = m.engine.snapshot()
    evs = labelled(records.events(cam_id, since, 60 if since else 40))
    return jsonify(snapshot=snap, status=m.status(), events=evs,
                   last_event_id=max([e["id"] for e in evs], default=since),
                   shift=records.shift_stats(cam_id), shift_info=records.current_shift(),
                   rois=m.rois, sound=cfg().section("alerts"))


@bp.get("/api/cameras/<cam_id>/events")
@role_required("viewer")
def events(cam_id):
    sev = request.args.get("severity")
    return jsonify(events=labelled(records.events(cam_id, 0, request.args.get("limit", 100, type=int), sev)))


@bp.get("/api/cameras/<cam_id>/alerts")
@role_required("viewer")
def alerts(cam_id):
    rows = [e for e in records.events(cam_id, 0, 300, "alert") if e["etype"] in VIOLATION_CODES or e["etype"] == "SOP_FAIL"]
    return jsonify(alerts=labelled(rows[:100]))


@bp.get("/api/cameras/<cam_id>/cycles")
@role_required("viewer")
def cycles(cam_id):
    return jsonify(cycles=records.cycles(cam_id, None, 100))


# ------------------------------------------------------------------ screenshots
@bp.post("/api/cameras/<cam_id>/screenshot")
@role_required("operator")
def screenshot(cam_id):
    name = mon_or_404(cam_id).save_screenshot("manual")
    if not name:
        return jsonify(error="no frame available yet"), 409
    audit("SCREENSHOT", name)
    return jsonify(file=name, url=f"/api/screenshots/{name}")


@bp.get("/api/screenshots/<path:name>")
@role_required("viewer")
def screenshot_file(name):
    return send_from_directory(SHOT_DIR, name, mimetype="image/jpeg")


# ------------------------------------------------------------------ ROIs
@bp.get("/api/cameras/<cam_id>/rois")
@role_required("viewer")
def get_rois(cam_id):
    c = cfg().camera(cam_id) or abort(404)
    return jsonify(rois=c["rois"])


@bp.put("/api/cameras/<cam_id>/rois")
@role_required("supervisor")
def put_rois(cam_id):
    try:
        mgr().update_rois(cam_id, body().get("rois", {}))
    except (ValueError, KeyError, TypeError) as e:
        return jsonify(error=f"invalid ROI data: {e}"), 400
    audit("ROI_SAVE", cam_id)
    return jsonify(ok=True, rois=cfg().camera(cam_id)["rois"])


# ------------------------------------------------------------------ simulator control
@bp.get("/api/simulator/scenarios")
@role_required("viewer")
def scenarios():
    return jsonify(scenarios=list(SCENARIOS))


@bp.post("/api/cameras/<cam_id>/simulate")
@role_required("supervisor")
def simulate(cam_id):
    m = mon_or_404(cam_id)
    if not m.source.is_simulator:
        return jsonify(error="camera is not a simulator"), 400
    b = body()
    if b.get("stop"):
        m.source.stop_scenario()
    elif "scenario" in b:
        try:
            m.source.run_scenario(b["scenario"])
        except KeyError:
            return jsonify(error="unknown scenario"), 400
    elif "zone" in b:
        m.source.go(b["zone"])
    audit("SIMULATE", str(b))
    return jsonify(ok=True)


# ------------------------------------------------------------------ settings
@bp.get("/api/settings")
@role_required("viewer")
def get_settings():
    c = cfg().get()
    return jsonify(sop=c["sop"], alerts=c["alerts"], detector=c["detector"], cameras=mgr().camera_list())


def _num(v, lo, hi, name):
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


@bp.put("/api/settings")
@role_required("supervisor")
def put_settings():
    b = body()
    try:
        if "sop" in b:
            s = b["sop"]
            clean = {"dwell_ms": _num(s["dwell_ms"], 50, 5000, "dwell_ms"),
                     "assembly_timeout_s": _num(s["assembly_timeout_s"], 5, 3600, "assembly_timeout_s"),
                     "idle_threshold_s": _num(s["idle_threshold_s"], 3, 3600, "idle_threshold_s"),
                     "fail_hold_s": _num(s["fail_hold_s"], 1, 60, "fail_hold_s"),
                     "pass_hold_s": _num(s["pass_hold_s"], 1, 60, "pass_hold_s")}
            cfg().update_section("sop", clean)
        if "alerts" in b:
            a = b["alerts"]
            clean = {"sound_enabled": bool(a.get("sound_enabled")),
                     "sound_volume": _num(a.get("sound_volume", 0.6), 0, 1, "sound_volume"),
                     "auto_screenshot": bool(a.get("auto_screenshot")),
                     "enabled_violations": [v for v in a.get("enabled_violations", []) if v in VIOLATION_CODES]}
            cfg().update_section("alerts", clean)
        if "detector" in b:
            if g.user["role"] != "admin":
                return jsonify(error="admin role required for detector settings"), 403
            d = b["detector"]
            cfg().update_section("detector", {"backend": d.get("backend", "auto"),
                                              "target_fps": int(_num(d.get("target_fps", 15), 1, 60, "target_fps")),
                                              "component_conf": _num(d.get("component_conf", 0.4), 0.05, 0.99, "component_conf")})
    except (ValueError, KeyError, TypeError) as e:
        return jsonify(error=str(e)), 400
    mgr().apply_settings()
    audit("SETTINGS_CHANGE", ", ".join(b.keys()))
    return jsonify(ok=True)


@bp.post("/api/cameras")
@role_required("admin")
def add_camera():
    b = body()
    cid = "".join(ch for ch in (b.get("id") or "") if ch.isalnum() or ch in "-_")[:24]
    if not cid or not b.get("source"):
        return jsonify(error="id and source are required"), 400
    cfg().upsert_camera({"id": cid, "name": b.get("name") or cid, "source": str(b["source"]), "enabled": True})
    mgr().reload_camera(cid)
    audit("CAMERA_SAVE", f"{cid} -> {b['source']}")
    return jsonify(ok=True)


@bp.delete("/api/cameras/<cam_id>")
@role_required("admin")
def del_camera(cam_id):
    try:
        cfg().delete_camera(cam_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    mgr().remove_camera(cam_id)
    audit("CAMERA_DELETE", cam_id)
    return jsonify(ok=True)


# ------------------------------------------------------------------ shifts
@bp.post("/api/shift/reset")
@role_required("supervisor")
def shift_reset():
    shift = mgr().reset_shift(g.user["username"])
    audit("SHIFT_RESET", f"new shift #{shift['id']}")
    return jsonify(ok=True, shift=shift)


@bp.get("/api/supervisor/overview")
@role_required("supervisor")
def overview():
    cams = []
    for c in mgr().camera_list():
        m = mgr().get(c["id"])
        snap = m.engine.snapshot() if m else None
        cams.append({**c, "snapshot": snap, "shift": records.shift_stats(c["id"])})
    return jsonify(cameras=cams, shift=records.shift_stats(), shift_info=records.current_shift(),
                   shifts=records.list_shifts(15), recent_alerts=labelled(records.events(None, 0, 25, "alert")))


# ------------------------------------------------------------------ exports
@bp.get("/api/export/events.csv")
@role_required("supervisor")
def export_events():
    rows = records.events(request.args.get("camera"), 0, 100000)
    audit("EXPORT", "events.csv")
    return csv_response("sop_events.csv", ["id", "timestamp", "camera", "event", "severity", "message", "cycle", "shift", "screenshot"],
                        [[r["id"], r["ts"], r["camera_id"], EVENT_LABELS.get(r["etype"], r["etype"]), r["severity"], r["message"],
                          r["cycle_no"], r["shift_id"], r["screenshot"]] for r in reversed(rows)])


@bp.get("/api/export/cycles.csv")
@role_required("supervisor")
def export_cycles():
    rows = records.cycles(request.args.get("camera"), None, 100000)
    audit("EXPORT", "cycles.csv")
    return csv_response("sop_cycles.csv", ["id", "camera", "shift", "cycle", "start", "end", "duration_s", "result", "violations"],
                        [[r["id"], r["camera_id"], r["shift_id"], r["cycle_no"], r["start_ts"], r["end_ts"], r["duration"],
                          r["result"], r["violations"]] for r in reversed(rows)])


@bp.get("/api/export/shift.csv")
@role_required("supervisor")
def export_shift():
    s = records.shift_stats(request.args.get("camera"))
    audit("EXPORT", "shift.csv")
    rows = [[k, v] for k, v in s.items() if k != "violations_by_type"] + [[f"violations:{k}", v] for k, v in s["violations_by_type"].items()]
    return csv_response("sop_shift_stats.csv", ["metric", "value"], rows)


@bp.get("/api/export/audit.csv")
@role_required("supervisor")
def export_audit():
    audit("EXPORT", "audit.csv")
    return csv_response("sop_audit_log.csv", ["id", "timestamp", "user", "action", "details", "ip"],
                        [[r["id"], r["ts"], r["username"], r["action"], r["details"], r["ip"]] for r in records.audit_log(100000)])


# ------------------------------------------------------------------ audit + users
@bp.get("/api/audit")
@role_required("supervisor")
def audit_list():
    return jsonify(audit=records.audit_log(500))


@bp.get("/api/users")
@role_required("admin")
def users_list():
    return jsonify(users=users_model.list_users(), roles=list(users_model.ROLES))


@bp.post("/api/users")
@role_required("admin")
def users_create():
    b = body()
    try:
        users_model.create_user(b.get("username", ""), b.get("full_name", ""), b.get("password", ""), b.get("role", "operator"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception:
        return jsonify(error="username already exists"), 409
    audit("USER_CREATE", f"{b.get('username')} ({b.get('role')})")
    return jsonify(ok=True)


@bp.put("/api/users/<int:uid>")
@role_required("admin")
def users_update(uid):
    b = body()
    if uid == g.user["id"] and (b.get("role") not in (None, "admin") or b.get("active") is False):
        return jsonify(error="you cannot demote or deactivate your own account"), 400
    try:
        users_model.update_user(uid, b.get("full_name"), b.get("role"), b.get("active"), b.get("password"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    audit("USER_UPDATE", f"id={uid} fields={[k for k in b if k != 'password']}{' +password' if b.get('password') else ''}")
    return jsonify(ok=True)


@bp.delete("/api/users/<int:uid>")
@role_required("admin")
def users_delete(uid):
    if uid == g.user["id"]:
        return jsonify(error="you cannot delete your own account"), 400
    users_model.delete_user(uid)
    audit("USER_DELETE", f"id={uid}")
    return jsonify(ok=True)


@bp.post("/api/account/password")
@role_required("viewer")
def change_password():
    b = body()
    if not users_model.authenticate(g.user["username"], b.get("current", "")):
        return jsonify(error="current password is incorrect"), 400
    try:
        users_model.update_user(g.user["id"], password=b.get("new", ""))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    audit("PASSWORD_CHANGE")
    return jsonify(ok=True)
