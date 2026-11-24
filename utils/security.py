import functools
import secrets
from flask import session, request, jsonify, redirect, url_for, g, abort

ROLE_LEVEL = {"viewer": 1, "operator": 2, "supervisor": 3, "admin": 4}


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.endpoint == "auth.login":
            return
        tok = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not tok or tok != session.get("csrf"):
            abort(400, "CSRF token missing or invalid")


def role_required(min_role: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            user = g.get("user")
            is_api = request.path.startswith("/api/")
            if not user:
                if is_api:
                    return jsonify(error="authentication required"), 401
                return redirect(url_for("auth.login", next=request.path))
            if ROLE_LEVEL.get(user["role"], 0) < ROLE_LEVEL[min_role]:
                if is_api:
                    return jsonify(error="insufficient role"), 403
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco
