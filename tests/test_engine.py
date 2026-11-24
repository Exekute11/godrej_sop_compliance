from services.sop_engine import SopEngine

CFG = {"dwell_ms": 100, "assembly_timeout_s": 30, "idle_threshold_s": 10, "fail_hold_s": 2, "pass_hold_s": 1}


class Rig:
    def __init__(self, **over):
        self.t = [0.0]
        self.ev, self.cy = [], []
        self.e = SopEngine("c", {**CFG, **over}, {}, self.ev.append, self.cy.append, clock=lambda: self.t[0])

    def tick(self, zones=(), secs=0.1, hand=None):
        for _ in range(max(1, int(secs / 0.05))):
            self.e.update(list(zones), bool(zones) if hand is None else hand)
            self.t[0] += 0.05

    def visit(self, z, dwell=0.3):
        self.tick([z], dwell)
        self.tick([], 0.6, hand=True)

    def types(self):
        return [x["etype"] for x in self.ev]


def run(*zones, **over):
    r = Rig(**over)
    for z in zones:
        r.visit(z)
    return r


def test_correct_sequence_passes():
    r = run(1, 2, 3, 4)
    assert r.types() == ["BOLT_PICKED", "SPRING_PICKED", "PLAIN_PICKED", "ASSEMBLY_COMPLETED", "SOP_PASS"]
    assert r.cy[-1]["result"] == "PASS" and r.e.snapshot()["counters"]["pass_rate"] == 100.0
    assert r.e.snapshot()["state"] == "CYCLE_COMPLETED"


def test_spring_skipped():
    r = run(1, 3, 4)
    t = r.types()
    assert "SPRING_SKIPPED" in t and "PLAIN_BEFORE_SPRING" in t and "SOP_FAIL" in t and "SOP_PASS" not in t


def test_plain_skipped():
    t = run(1, 2, 4).types()
    assert "PLAIN_SKIPPED" in t and "ASSEMBLY_INCOMPLETE" in t and t[-1] == "SOP_FAIL"


def test_bolt_only_to_assembly():
    t = run(1, 4).types()
    assert {"SPRING_SKIPPED", "PLAIN_SKIPPED", "ASSEMBLY_INCOMPLETE", "STATION_BYPASS"} <= set(t)


def test_wrong_sequence_return_to_spring():
    t = run(1, 2, 3, 2).types()
    assert "WRONG_SEQUENCE" in t and "SEQUENCE_BROKEN" in t


def test_component_dropped_restart():
    t = run(1, 2, 1).types()
    assert "COMPONENT_DROPPED" in t and t[-1] == "SOP_FAIL"


def test_station_bypass_without_bolt():
    t = run(3).types()
    assert "STATION_BYPASS" in t and "MISSING_COMPONENT" in t


def test_assembly_timeout():
    r = Rig(assembly_timeout_s=5)
    r.visit(1)
    r.tick([], 7, hand=True)
    assert "ASSEMBLY_TIMEOUT" in r.types() and r.cy[-1]["result"] == "FAIL"


def test_idle_alert_and_time():
    r = Rig(idle_threshold_s=3)
    r.tick([], 8, hand=False)
    assert r.types().count("OPERATOR_IDLE") == 1
    assert r.e.snapshot()["counters"]["idle_time"] > 3


def test_dwell_filters_brief_crossings():
    r = Rig()
    r.tick([1], 0.05)
    r.tick([], 0.6, hand=True)
    assert r.types() == []


def test_recovers_after_fail():
    r = run(1, 3, 4)
    r.tick([], 3, hand=True)
    for z in (1, 2, 3, 4):
        r.visit(z)
    assert r.types()[-1] == "SOP_PASS" and r.e.cycles_passed == 1 and r.e.cycles_failed == 1


def test_disabled_violation_is_ignored():
    r = Rig()
    r.e.configure(alert_cfg={"enabled_violations": ["ASSEMBLY_TIMEOUT"]})
    for z in (1, 3, 4):
        r.visit(z)
    assert "SPRING_SKIPPED" not in r.types()


def test_ai_component_verification_fails_empty_assembly():
    r = Rig()
    for z in (1, 2, 3):
        r.visit(z)
    for _ in range(8):
        r.e.update([4], True, components={4: set()})
        r.t[0] += 0.05
    assert "MISSING_COMPONENT" in r.types()


def test_next_cycle_can_start_during_pass_hold():
    r = Rig(pass_hold_s=5)
    for z in (1, 2, 3, 4):
        r.visit(z)
    r.visit(1)
    assert r.types()[-1] == "BOLT_PICKED" and r.e.snapshot()["progress"] == 1


def test_bolt_pick_during_fail_hold_registers_after_hold():
    r = run(1, 3, 4, fail_hold_s=2)
    r.tick([1], 3.0)          # hand sits in bolt bin through the hold
    assert r.types()[-1] == "BOLT_PICKED"
