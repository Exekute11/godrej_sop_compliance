"""Events, cycles, shifts and audit-log data access."""
from database import db
from utils.timeutil import now_iso


# ---- shifts ----
def current_shift():
    s = db.query_one("SELECT * FROM shifts WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1")
    if not s:
        sid = db.execute("INSERT INTO shifts(name,start_ts,started_by) VALUES(?,?,?)", ("Shift", now_iso(), "system"))
        s = db.query_one("SELECT * FROM shifts WHERE id=?", (sid,))
    return s


def reset_shift(user, summary):
    s = current_shift()
    db.execute("UPDATE shifts SET end_ts=?, summary=? WHERE id=?", (now_iso(), db.dumps(summary), s["id"]))
    sid = db.execute("INSERT INTO shifts(name,start_ts,started_by) VALUES(?,?,?)", ("Shift", now_iso(), user))
    return db.query_one("SELECT * FROM shifts WHERE id=?", (sid,))


def list_shifts(limit=30):
    return db.query("SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,))


# ---- events ----
def add_event(camera_id, etype, severity, message, cycle_no=None, screenshot=None):
    return db.execute(
        "INSERT INTO events(ts,camera_id,etype,severity,message,cycle_no,shift_id,screenshot) VALUES(?,?,?,?,?,?,?,?)",
        (now_iso(), camera_id, etype, severity, message, cycle_no, current_shift()["id"], screenshot))


def set_event_screenshot(event_id, name):
    db.execute("UPDATE events SET screenshot=? WHERE id=?", (name, event_id))


def events(camera_id=None, since_id=0, limit=100, severity=None, shift_id=None):
    sql, p = "SELECT * FROM events WHERE id>?", [since_id]
    if camera_id:
        sql += " AND camera_id=?"; p.append(camera_id)
    if severity:
        sql += " AND severity=?"; p.append(severity)
    if shift_id:
        sql += " AND shift_id=?"; p.append(shift_id)
    sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
    return db.query(sql, p)


# ---- cycles ----
def add_cycle(camera_id, cycle_no, start_ts, duration, result, violations):
    return db.execute(
        "INSERT INTO cycles(camera_id,shift_id,cycle_no,start_ts,end_ts,duration,result,violations) VALUES(?,?,?,?,?,?,?,?)",
        (camera_id, current_shift()["id"], cycle_no, start_ts, now_iso(), duration, result, db.dumps(violations)))


def cycles(camera_id=None, shift_id=None, limit=500):
    sql, p = "SELECT * FROM cycles WHERE 1=1", []
    if camera_id:
        sql += " AND camera_id=?"; p.append(camera_id)
    if shift_id:
        sql += " AND shift_id=?"; p.append(shift_id)
    sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
    return db.query(sql, p)


def shift_stats(camera_id=None, shift_id=None):
    shift_id = shift_id or current_shift()["id"]
    cyc = cycles(camera_id, shift_id, 100000)
    total = len(cyc)
    passed = sum(1 for c in cyc if c["result"] == "PASS")
    durs = [c["duration"] for c in cyc if c["result"] == "PASS" and c["duration"]]
    from models.sop import VIOLATION_CODES
    ev = [e for e in events(camera_id, 0, 100000, "alert", shift_id) if e["etype"] in VIOLATION_CODES]
    by_type = {}
    for e in ev:
        by_type[e["etype"]] = by_type.get(e["etype"], 0) + 1
    return {"shift_id": shift_id, "cycles": total, "passed": passed, "failed": total - passed,
            "pass_rate": round(100.0 * passed / total, 1) if total else 0.0,
            "avg_cycle_time": round(sum(durs) / len(durs), 1) if durs else 0.0,
            "violations": len(ev), "violations_by_type": by_type}


# ---- audit ----
def audit(username, action, details="", ip=""):
    db.execute("INSERT INTO audit(ts,username,action,details,ip) VALUES(?,?,?,?,?)",
               (now_iso(), username, action, details, ip))


def audit_log(limit=500):
    return db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
