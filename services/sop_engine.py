"""SOP state machine.

Zone + AI hybrid: the detector reports which ROI zones contain a hand (and, when a YOLO
component model is available, which components are visible in each zone). This engine turns
those observations into SOP events, violations and cycle results.

Progress p = number of SOP steps completed in the active cycle:
    0 -> waiting for bolt (Zone 1)      1 -> waiting for spring washer (Zone 2)
    2 -> waiting for plain washer (Z3)  3 -> waiting for assembly placement (Zone 4)
The engine is clock-injectable and fully thread-safe.
"""
import datetime
import logging
import threading
import time
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

from models.sop import (STAGES, STATE_COMPLETED, STATE_FAILED, VIOLATION_LABELS, VIOLATION_CODES)

log = logging.getLogger("sop.engine")

console = Console()

RELEASE_MS = 400  # zone must be empty this long before it can fire again


class SopEngine:
    def __init__(
        self,
        camera_id,
        sop_cfg,
        alert_cfg,
        on_event=None,
        on_cycle=None,
        on_result=None,
        clock=time.time,
    ):
        self.camera_id = camera_id
        self.sop = dict(sop_cfg)
        self.alerts = dict(alert_cfg)
        self.on_event = on_event or (lambda e: None)
        self.on_cycle = on_cycle or (lambda c: None)

        self.on_result = on_result or (
            lambda result, cycle_no: None
        )

        self.clock = clock
        self._lock = threading.RLock()
        self._last_update = None
        self.reset_shift_counters(emit=False)
        self._terminal_cycle_active = False
        self._result_sent_cycle = None

    # ------------------------------------------------------------------ config
    def configure(self, sop_cfg=None, alert_cfg=None):
        with self._lock:
            if sop_cfg:
                self.sop.update(sop_cfg)
            if alert_cfg:
                self.alerts.update(alert_cfg)

    # ------------------------------------------------------------------ counters
    def reset_shift_counters(self, emit=True):
        with self._lock:
            now = self.clock()
            self.cycles_total = 0
            self.cycles_passed = 0
            self.cycles_failed = 0
            self.pass_durations = []
            self.violations_total = 0
            self.violations_by_type = {}
            self.idle_total = 0.0
            self.shift_start = now
            self._reset_cycle(now)
            self._zone_since, self._zone_seen, self._zone_fired = {}, {}, {}
            self._last_activity = now
            self._idle_flagged = False
            self.cycle_no = 0
            self.last_alert = None
            self.occupied = set()

    def _reset_cycle(self, now):
        self.progress = 0
        self.state = "WAITING"          # WAITING | ACTIVE | PASSED | FAILED
        self.cycle_start = None
        self.cycle_violations = []
        self.hold_until = None
        self.failed_step = None
        self._cycle_started_wall = None

    # ------------------------------------------------------------------ events
    def _emit(self, etype, severity, message):
        """
        Save the event for the dashboard and database without printing
        intermediate SOP messages in the terminal.
        """

        ev = {
            "camera_id": self.camera_id,
            "etype": etype,
            "severity": severity,
            "message": message,
            "cycle_no": self.cycle_no,
            "t": self.clock(),
        }

        try:
            self.on_event(ev)

        except Exception:
            # Do not terminate inspection if database event saving fails.
            pass

        return ev

    def _violate(self, code, detail=""):
        if code not in self.alerts.get("enabled_violations", VIOLATION_CODES):
            return False
        msg = VIOLATION_LABELS[code] + (f" - {detail}" if detail else "")
        self.violations_total += 1
        self.violations_by_type[code] = self.violations_by_type.get(code, 0) + 1
        self.cycle_violations.append(code)
        self.last_alert = {"code": code, "message": msg, "t": self.clock()}
        self._emit(code, "alert", msg)
        return True

    # ------------------------------------------------------------------ main entry
    def update(self, zones_occupied, hand_present=True, components=None):
        """Feed one observation. zones_occupied: iterable of zone ids (1-4) holding a hand."""
        with self._lock:
            now = self.clock()
            dt = 0.0 if self._last_update is None else max(0.0, min(now - self._last_update, 2.0))
            self._last_update = now
            self.occupied = set(zones_occupied)

            # hold expiry (after PASS/FAIL)
            if self.hold_until is not None and now >= self.hold_until:
                self._reset_cycle(now)

            fired = self._debounce(now)
            if zones_occupied or hand_present:
                self._last_activity = now
                self._idle_flagged = False
            for z in fired:
                self._last_activity = now
                self._on_enter(z, now, components)

            self._check_timeout(now)
            self._check_idle(now, dt)

    def _debounce(self, now):
        dwell = self.sop.get("dwell_ms", 250) / 1000.0
        fired = []
        for z in (1, 2, 3, 4):
            if z in self.occupied:
                self._zone_seen[z] = now
                self._zone_since.setdefault(z, now)
                if not self._zone_fired.get(z) and now - self._zone_since[z] >= dwell:
                    self._zone_fired[z] = True
                    fired.append(z)
            else:
                last = self._zone_seen.get(z)
                if last is not None and now - last >= RELEASE_MS / 1000.0:
                    self._zone_since.pop(z, None)
                    self._zone_fired[z] = False
                    self._zone_seen.pop(z, None)
        fired.sort(key=lambda z: self._zone_since.get(z, now))
        return fired

    # ------------------------------------------------------------------ SOP logic
    def _terminal_cycle_start(self):
        """Start a clean sequential SOP display."""

        self._terminal_cycle_active = True

        console.print()
        console.print(
            f"[bold bright_magenta]OUTPUT - CYCLE {self.cycle_no}[/bold bright_magenta]"
        )
        console.print()


    def _terminal_step(self, step_name, color="bright_white"):
        """Print one SOP step only when that action is detected."""

        if not self._terminal_cycle_active:
            return

        console.print(
            f"[bold {color}]{step_name}[/bold {color}]"
        )


    def _terminal_arrow(self):
        """Print the sequence arrow between confirmed actions."""

        if not self._terminal_cycle_active:
            return

        console.print(
            "[bold bright_magenta]  ↓[/bold bright_magenta]"
        )


    def _terminal_fail(self, duration, reason):
        """Print the final failed-cycle result."""

        console.print()
        console.print(
            (
                f"[bold bright_red]✗ SOP FAIL[/bold bright_red]"
                f"  [white]| Cycle {self.cycle_no}"
                f" | {duration:.2f} seconds[/white]"
            )
        )

        console.print(
            f"[bright_red]Reason: {reason}[/bright_red]"
        )

        if self.cycle_violations:
            console.print(
                "[yellow]Violations: "
                + ", ".join(self.cycle_violations)
                + "[/yellow]"
            )

        console.print(
            "[dim]Serial result: FAIL = 1[/dim]"
        )
        console.print()

        self._terminal_cycle_active = False

    def _start_cycle(self, now):
        self.cycle_no += 1
        self._result_sent_cycle = None

        self.state = "ACTIVE"
        self.cycle_start = now
        self.cycle_violations = []
        self._cycle_started_wall = datetime.datetime.now().isoformat(timespec="seconds")

        

    def _on_enter(self, z, now, components):
        if self.state == "PASSED" and z == 1:
            self._reset_cycle(now)          # operator already started the next unit - don't swallow it
        elif self.state in ("PASSED", "FAILED"):
            if z == 1:
                self._zone_fired[1] = False  # re-arm: will register as a fresh bolt pick when the hold ends
            return  # result is being displayed
        p = self.progress
        if self.state == "WAITING" and z != 1:
            self._start_cycle(now)
            self._bypass_from_idle(z, now)
            return
        if self.state == "WAITING" and z == 1:
            self._start_cycle(now)
            self.progress = 1

            self._emit(
                "BOLT_PICKED",
                "info",
                "Bolt action detected in Bolt Bin"
            )

            self._terminal_cycle_start()
            self._terminal_step(
                "BOLT",
                "bright_white"
            )

            return

        # ACTIVE cycle
        if z == p + 1:
            if z == 2:
                self.progress = 2

                self._emit(
                    "SPRING_PICKED",
                    "info",
                    "Spring Washer Zone action detected"
                )
                self._terminal_arrow()
                self._terminal_step(
                    "SPRING WASHER",
                    "cyan"
                )

            elif z == 3:
                self.progress = 3

                self._emit(
                    "PLAIN_PICKED",
                    "info",
                    "Plain Washer Zone action detected"
                )

                self._terminal_arrow()
                self._terminal_step(
                    "FLAT WASHER",
                    "yellow"
                )


            elif z == 4:
                self._terminal_arrow()
                self._terminal_step(
                    "ASSEMBLY AREA",
                    "bright_white"
                )

                self._assembly(
                    now,
                    components
                )

            return

        if z == p:
            return  # same station revisited - ignore (re-grip)
        if z < p:
            if z == 1:
                self._violate("COMPONENT_DROPPED", "operator returned to Bolt Bin mid-cycle")
                self._fail(now, "Component dropped, workflow restarted")
            else:
                self._violate("WRONG_SEQUENCE", f"returned to {self._zname(z)} after {self._zname(p)}")
                self._fail(now, "Sequence broken", sequence=True)
            return
        # z > p + 1  -> stations skipped
        skipped = list(range(p + 1, z))
        if p == 1 and z == 3:
            self._violate("SPRING_SKIPPED", "spring washer not picked")
            self._violate("PLAIN_BEFORE_SPRING")
            self._violate("WRONG_SEQUENCE", "Bolt -> Plain Washer")
        elif p == 1 and z == 4:
            self._violate("SPRING_SKIPPED", "spring washer not picked")
            self._violate("PLAIN_SKIPPED", "plain washer not picked")
            self._violate("ASSEMBLY_INCOMPLETE", "assembly placed with bolt only")
            self._violate("STATION_BYPASS", "Bolt -> Assembly")
        elif p == 2 and z == 4:
            self._violate("PLAIN_SKIPPED", "plain washer not picked")
            self._violate("ASSEMBLY_INCOMPLETE", "assembly placed without plain washer")
        else:  # defensive fallback for any future station layout
            self._violate("STATION_BYPASS", f"skipped {skipped}")
        self._fail(now, "Sequence broken", sequence=True)

    def _bypass_from_idle(self, z, now):
        """Operator went to Zone 2/3/4 without first picking a bolt."""
        self._violate("STATION_BYPASS", f"started at {self._zname(z)} without a bolt")
        self._violate("MISSING_COMPONENT", "Bolt")
        if z == 4:
            self._violate("ASSEMBLY_INCOMPLETE", "nothing assembled")
        self._fail(now, "Sequence broken", sequence=True)

    def _assembly(self, now, components):
        # AI verification: if a component model is running, a bolt must be visible in the assembly ROI.
        if components is not None and "bolt" not in components.get(4, set()):
            self._violate("MISSING_COMPONENT", "no assembly detected in Assembly Area")
            self._fail(now, "Missing component")
            return
        self.progress = 4
        self._emit("ASSEMBLY_COMPLETED", "success", "Assembly placed in Assembly Area")
        self._pass(now)

    def _check_timeout(self, now):
        if self.state == "ACTIVE" and self.cycle_start is not None:
            limit = self.sop.get("assembly_timeout_s", 60)
            if now - self.cycle_start > limit:
                self._violate("ASSEMBLY_TIMEOUT", f"cycle exceeded {limit}s")
                self._fail(now, "Assembly timeout")

    def _check_idle(self, now, dt):
        thr = float(
            self.sop.get("idle_threshold_s", 20)
        )

        # Zero or negative disables idle monitoring.
        if thr <= 0:
            self._idle_flagged = False
            return

        idle_for = now - self._last_activity

        if idle_for > thr:
            self.idle_total += dt

            if not self._idle_flagged:
                self._idle_flagged = True

                self._violate(
                    "OPERATOR_IDLE",
                    f"no activity for {int(thr)}s"
                )
    def _terminal_pass(self, duration):
        console.print()

        console.print(
            (
                f"[bold bright_green]✓ SOP PASS[/bold bright_green]"
                f"  [white]| Cycle {self.cycle_no}"
                f" | {duration:.2f} seconds[/white]"
            )
        )

        console.print(
            "[dim]Serial result: PASS = 0[/dim]"
        )

        console.print()

        self._terminal_cycle_active = False

    # ------------------------------------------------------------------ results
    def _send_final_result(self, result):
        """
        Send the final machine result only once per cycle.

        PASS -> 0
        FAIL -> 1
        """

        if self._result_sent_cycle == self.cycle_no:
            return

        self._result_sent_cycle = self.cycle_no

        try:
            self.on_result(
                result,
                self.cycle_no
            )
        except Exception:
            # A serial error must not stop the AI/camera pipeline.
            pass
    def _pass(self, now):
        dur = now - (self.cycle_start or now)

        self.cycles_total += 1
        self.cycles_passed += 1
        self.pass_durations.append(dur)

        self.state = "PASSED"
        self.hold_until = (
            now + self.sop.get("pass_hold_s", 3)
        )

        self._emit(
            "SOP_PASS",
            "success",
            (
                f"SOP PASS - cycle {self.cycle_no} "
                f"completed in {dur:.1f}s"
            )
        )

        # BOLT, SPRING WASHER, FLAT WASHER and ASSEMBLY AREA
        # are printed individually when their actions are detected.
        # This prints only the final result.
        self._terminal_pass(dur)

        # Send ASCII 0 once for this PASS cycle.
        self._send_final_result("PASS")

        self._record(
            now,
            dur,
            "PASS"
        )

    def _fail(self, now, reason, sequence=False):
        dur = now - (self.cycle_start or now)

        self.cycles_total += 1
        self.cycles_failed += 1

        self.failed_step = self.progress
        self.state = "FAILED"

        self.hold_until = (
            now + self.sop.get("fail_hold_s", 4)
        )

        if sequence:
            self._emit(
                "SEQUENCE_BROKEN",
                "warn",
                "Sequence broken - SOP order not followed"
            )

        self._emit(
            "SOP_FAIL",
            "alert",
            f"SOP FAIL - {reason}"
        )

        self._terminal_fail(
            dur,
            reason
        )

        # Send ASCII 1 once for this FAIL cycle.
        self._send_final_result("FAIL")

        self._record(
            now,
            dur,
            "FAIL"
        )

    def _record(self, now, dur, result):
        try:
            self.on_cycle({"camera_id": self.camera_id, "cycle_no": self.cycle_no, "duration": round(dur, 2),
                           "result": result, "violations": list(self.cycle_violations),
                           "start_wall": self._cycle_started_wall})
        except Exception:
            pass

    def _zname(self, z):
        names = self.sop.get("step_names", ["Bolt", "Spring Washer", "Plain Washer", "Assembly"])
        return names[z - 1] if 0 < z <= len(names) else f"Zone {z}"

    # ------------------------------------------------------------------ snapshot
    def snapshot(self):
        with self._lock:
            now = self.clock()
            p = self.progress
            if self.state == "PASSED":
                key, label = STATE_COMPLETED
            elif self.state == "FAILED":
                key, label = STATE_FAILED
            else:
                key, label = STAGES[min(p, 3)]
            steps = []
            for i, name in enumerate(self.sop.get("step_names", ["Bolt", "Spring Washer", "Plain Washer", "Assembly"])):
                if self.state == "PASSED":
                    st = "done"
                elif self.state == "FAILED":
                    st = "done" if i < (self.failed_step or 0) else ("failed" if i == (self.failed_step or 0) else "pending")
                else:
                    st = "done" if i < p else ("current" if i == p else "pending")
                steps.append({"name": name, "status": st})
            elapsed = (now - self.cycle_start) if (self.cycle_start and self.state == "ACTIVE") else 0.0
            if self.state == "FAILED":
                banner = {"level": "alert", "title": "SOP VIOLATION", "tag": "FAIL",
                          "text": (self.last_alert or {}).get("message", "SOP not followed")}
            elif self.state == "PASSED":
                banner = {"level": "ok", "title": "SOP PASS", "tag": "PASS",
                          "text": f"Cycle {self.cycle_no} completed in line with SOP"}
            elif now - self._last_activity > self.sop.get("idle_threshold_s", 20):
                banner = {"level": "idle", "title": "Operator Idle", "tag": "IDLE",
                          "text": f"No activity for {int(now - self._last_activity)}s"}
            elif self.state == "ACTIVE":
                banner = {"level": "ok", "title": "SOP In Progress", "tag": "RUNNING", "text": label}
            else:
                banner = {"level": "ok", "title": "Ready", "tag": "READY", "text": "Waiting for operator to start a cycle"}
            avg = sum(self.pass_durations) / len(self.pass_durations) if self.pass_durations else 0.0
            return {
                "camera_id": self.camera_id, "state": key, "stage_label": label, "progress": p,
                "steps": steps, "banner": banner, "occupied": sorted(self.occupied),
                "cycle": {"no": self.cycle_no, "elapsed": round(elapsed, 1),
                          "timeout": self.sop.get("assembly_timeout_s", 60), "active": self.state == "ACTIVE"},
                "counters": {"cycles_total": self.cycles_total, "cycles_passed": self.cycles_passed,
                             "cycles_failed": self.cycles_failed,
                             "pass_rate": round(100.0 * self.cycles_passed / self.cycles_total, 1) if self.cycles_total else 0.0,
                             "avg_cycle_time": round(avg, 1), "violations": self.violations_total,
                             "violations_by_type": dict(self.violations_by_type),
                             "idle_time": round(self.idle_total, 1),
                             "idle_now": round(max(0.0, now - self._last_activity), 1)},
                "last_alert": self.last_alert,
                "alert_seq": self.violations_total,
            }
