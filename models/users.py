from werkzeug.security import generate_password_hash, check_password_hash
from database import db
from utils.timeutil import now_iso

ROLES = ("viewer", "operator", "supervisor", "admin")
DEFAULT_USERS = [
    ("admin", "System Administrator", "admin123", "admin"),
    ("supervisor", "Shift Supervisor", "super123", "supervisor"),
    ("operator", "Line Operator", "operator123", "operator"),
]


def seed_default_users():
    if db.query_one("SELECT id FROM users LIMIT 1"):
        return
    for u, n, p, r in DEFAULT_USERS:
        create_user(u, n, p, r)


def create_user(username, full_name, password, role):
    if role not in ROLES:
        raise ValueError("invalid role")
    if not username or len(password or "") < 6:
        raise ValueError("username required and password must be at least 6 characters")
    return db.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                      (username.strip().lower(), full_name, generate_password_hash(password), role, now_iso()))


def authenticate(username, password):
    u = db.query_one("SELECT * FROM users WHERE username=? AND active=1", ((username or "").strip().lower(),))
    if u and check_password_hash(u["password_hash"], password or ""):
        db.execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), u["id"]))
        return u
    return None


def get(uid):
    return db.query_one("SELECT id,username,full_name,role,active FROM users WHERE id=?", (uid,))


def list_users():
    return db.query("SELECT id,username,full_name,role,active,created_at,last_login FROM users ORDER BY id")


def update_user(uid, full_name=None, role=None, active=None, password=None):
    if role is not None and role not in ROLES:
        raise ValueError("invalid role")
    if full_name is not None:
        db.execute("UPDATE users SET full_name=? WHERE id=?", (full_name, uid))
    if role is not None:
        db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    if active is not None:
        db.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))
    if password:
        if len(password) < 6:
            raise ValueError("password must be at least 6 characters")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), uid))


def delete_user(uid):
    db.execute("DELETE FROM users WHERE id=?", (uid,))
