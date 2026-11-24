import datetime as dt


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def fmt_secs(s: float) -> str:
    s = int(round(s or 0))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
