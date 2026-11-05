"""The Pimoroni Pan-Tilt HAT is write-only -- it has no "where am I pointed
right now" query, it only accepts new angle commands. So if daveweb's
cameras page wants to show the current pan/tilt position (e.g. to draw a
little on-screen indicator, or just to not lie about where the window cam
is aimed), this process has to be the one source of truth: every move goes
through here first, state updates in memory, then the hardware call fires.

In-memory and reset-on-restart is fine -- worst case after a reboot the
window cam just reports (0, 0) until the next manual move, which also
happens to match where pantilthat itself centers a fresh HAT on first use.
"""
import threading

_lock = threading.Lock()
_pan = 0
_tilt = 0


def get() -> dict:
    with _lock:
        return {"pan": _pan, "tilt": _tilt}


def set(pan: int, tilt: int) -> None:
    global _pan, _tilt
    with _lock:
        _pan, _tilt = pan, tilt
