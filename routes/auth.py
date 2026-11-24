import time
from flask import Blueprint, render_template, request, redirect, session, url_for, g, jsonify

from models import users as users_model, records

bp = Blueprint("auth", __name__)
_FAILS = {}  # (ip, username) -> (count, locked_until)


def _locked(key):
    c, until = _FAILS.get(key, (0, 0))
    return until > time.time()


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        key = (request.remote_addr, username)
        if _locked(key):
            error = "Too many failed attempts. Try again in a minute."
        else:
            u = users_model.authenticate(username, request.form.get("password"))
            if u:
                _FAILS.pop(key, None)
                session.clear()
                session["uid"] = u["id"]
                session.permanent = True
                records.audit(u["username"], "LOGIN", "", request.remote_addr)
                nxt = request.args.get("next") or url_for("pages.dashboard")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("pages.dashboard"))
            c = _FAILS.get(key, (0, 0))[0] + 1
            _FAILS[key] = (c, time.time() + 60 if c >= 5 else 0)
            records.audit(username or "?", "LOGIN_FAILED", "", request.remote_addr)
            error = "Invalid username or password."
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    if g.get("user"):
        records.audit(g.user["username"], "LOGOUT", "", request.remote_addr)
    session.clear()
    return redirect(url_for("auth.login"))
