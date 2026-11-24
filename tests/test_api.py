import os, shutil, tempfile, time, json
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfgp = tmp_path / "config.json"
    shutil.copy(os.path.join(root, "config.json"), cfgp)
    monkeypatch.setenv("SOP_DB_PATH", str(tmp_path / "t.db"))
    from database import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    from app import create_app
    app = create_app(str(cfgp), start_cameras=True, testing=True)
    c = app.test_client()
    yield c
    app.extensions["manager"].stop_all()


def login(c, u="supervisor", p="super123"):
    r = c.post("/login", data={"username": u, "password": p})
    assert r.status_code == 302
    page = c.get("/dashboard")
    assert page.status_code == 200
    import re
    return re.search(r'csrf-token" content="(\w+)"', page.get_data(as_text=True)).group(1)


def test_auth_required(client):
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/dashboard").status_code == 302
    assert client.post("/login", data={"username": "x", "password": "y"}).status_code == 200


def test_roles(client):
    tok = login(client, "operator", "operator123")
    h = {"X-CSRF-Token": tok}
    assert client.get("/zones").status_code == 403
    assert client.get("/api/users").status_code == 403
    assert client.put("/api/cameras/cam1/rois", json={}, headers=h).status_code == 403
    assert client.post("/api/cameras/cam1/screenshot", headers=h).status_code in (200, 409)


def test_csrf_enforced(client):
    login(client)
    assert client.post("/api/shift/reset").status_code == 400


def test_roi_validation_and_save(client):
    h = {"X-CSRF-Token": login(client)}
    bad = client.put("/api/cameras/cam1/rois", json={"rois": {"1": {"name": "x", "x": 0.9, "y": 0, "w": 0.5, "h": 0.1}}}, headers=h)
    assert bad.status_code == 400
    ok = client.put("/api/cameras/cam1/rois", json={"rois": {"1": {"name": "Bolt Bin X", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}}, headers=h)
    assert ok.status_code == 200 and ok.get_json()["rois"]["1"]["name"] == "Bolt Bin X"


def wait_for(c, pred, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get("/api/cameras/cam1/state").get_json()
        if pred(d):
            return d
        time.sleep(0.3)
    raise AssertionError("timeout waiting for state")


def test_full_simulated_pass_and_fail_flow(client):
    h = {"X-CSRF-Token": login(client)}
    client.post("/api/cameras/cam1/simulate", json={"scenario": "correct"}, headers=h)
    d = wait_for(client, lambda d: d["shift"]["passed"] >= 1)
    assert d["shift"]["pass_rate"] == 100.0
    client.post("/api/cameras/cam1/simulate", json={"scenario": "skip_spring"}, headers=h)
    d = wait_for(client, lambda d: d["shift"]["failed"] >= 1)
    labels = [e["label"] for e in d["events"]]
    assert "Missing Spring Washer" in labels and "SOP FAIL" in labels
    assert d["shift"]["violations"] >= 2
    csv = client.get("/api/export/events.csv")
    assert csv.status_code == 200 and b"SOP PASS" in csv.data
    assert client.get("/video_feed/cam1", buffered=False).status_code == 200
    shot = client.post("/api/cameras/cam1/screenshot", headers=h)
    assert shot.status_code == 200 and client.get(shot.get_json()["url"]).status_code == 200
    client.post("/api/shift/reset", headers=h)
    assert client.get("/api/cameras/cam1/state").get_json()["shift"]["cycles"] == 0
    actions = [a["action"] for a in client.get("/api/audit").get_json()["audit"]]
    assert "SHIFT_RESET" in actions and "SIMULATE" in actions and "LOGIN" in actions


def test_user_management(client):
    h = {"X-CSRF-Token": login(client, "admin", "admin123")}
    assert client.post("/api/users", json={"username": "nina", "password": "secret1", "role": "viewer", "full_name": "N"}, headers=h).status_code == 200
    assert client.post("/api/users", json={"username": "nina", "password": "secret1", "role": "viewer"}, headers=h).status_code == 409
    uid = [u for u in client.get("/api/users").get_json()["users"] if u["username"] == "nina"][0]["id"]
    assert client.put(f"/api/users/{uid}", json={"role": "supervisor"}, headers=h).status_code == 200
    assert client.delete(f"/api/users/{uid}", headers=h).status_code == 200
    me = [u for u in client.get("/api/users").get_json()["users"] if u["username"] == "admin"][0]["id"]
    assert client.delete(f"/api/users/{me}", headers=h).status_code == 400


def test_pages_render(client):
    login(client, "admin", "admin123")
    for p in ("/dashboard", "/zones", "/settings", "/supervisor", "/users", "/audit"):
        assert client.get(p).status_code == 200, p
