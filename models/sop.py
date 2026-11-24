"""SOP domain constants shared by engine, API and UI."""

STAGES = [
    # progress -> (state key, human label)
    ("WAITING_BOLT", "Waiting For Bolt"),
    ("WAITING_SPRING", "Waiting For Spring Washer"),
    ("WAITING_PLAIN", "Waiting For Plain Washer"),
    ("ASSEMBLY_VERIFICATION", "Assembly Verification"),
]
STATE_COMPLETED = ("CYCLE_COMPLETED", "Cycle Completed")
STATE_FAILED = ("CYCLE_FAILED", "Cycle Failed - Resetting")

VIOLATION_LABELS = {
    "SPRING_SKIPPED": "Missing Spring Washer",
    "PLAIN_SKIPPED": "Missing Plain Washer",
    "WRONG_SEQUENCE": "Wrong Sequence",
    "PLAIN_BEFORE_SPRING": "Plain Washer Picked Before Spring Washer",
    "ASSEMBLY_INCOMPLETE": "Assembly Placed Incomplete",
    "ASSEMBLY_TIMEOUT": "Assembly Timeout",
    "STATION_BYPASS": "Station Sequence Bypassed",
    "MISSING_COMPONENT": "Missing Component",
    "COMPONENT_DROPPED": "Component Dropped - Workflow Restarted",
    "OPERATOR_IDLE": "Operator Idle Beyond Threshold",
}
VIOLATION_CODES = tuple(VIOLATION_LABELS)

EVENT_LABELS = {
    "BOLT_PICKED": "Bolt Picked",
    "SPRING_PICKED": "Spring Washer Picked",
    "PLAIN_PICKED": "Plain Washer Picked",
    "ASSEMBLY_COMPLETED": "Assembly Completed",
    "SOP_PASS": "SOP PASS",
    "SOP_FAIL": "SOP FAIL",
    "SEQUENCE_BROKEN": "Sequence Broken",
    "SHIFT_RESET": "Shift Reset",
    "CAMERA_ONLINE": "Camera Online",
    "CAMERA_OFFLINE": "Camera Offline",
    **VIOLATION_LABELS,
}
