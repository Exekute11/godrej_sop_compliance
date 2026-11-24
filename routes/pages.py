from flask import Blueprint, render_template, redirect, url_for, g

from models.sop import EVENT_LABELS, VIOLATION_LABELS
from utils.security import role_required

bp = Blueprint("pages", __name__)


@bp.route("/")
def root():
    return redirect(url_for("pages.dashboard"))


@bp.route("/dashboard")
@role_required("viewer")
def dashboard():
    return render_template("dashboard.html", labels=EVENT_LABELS)


@bp.route("/zones")
@role_required("supervisor")
def zones():
    return render_template("zones.html")


@bp.route("/settings")
@role_required("supervisor")
def settings():
    return render_template("settings.html", violations=VIOLATION_LABELS)


@bp.route("/supervisor")
@role_required("supervisor")
def supervisor():
    return render_template("supervisor.html", labels=EVENT_LABELS)


@bp.route("/users")
@role_required("admin")
def users():
    return render_template("users.html")


@bp.route("/audit")
@role_required("supervisor")
def audit():
    return render_template("audit.html")
