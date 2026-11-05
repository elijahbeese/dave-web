"""motion_watcher -- watches the door camera's own ISAPI alertStream for
motion-detection events and reacts: saves a short RTSP clip to local disk
and fires an ntfy.sh push. Runs as its own systemd service, separate from
camera_bridge, since it holds a long-lived blocking HTTP connection rather
than serving requests.

Needs the camera's own built-in motion detection ENABLED first (ISAPI
/ISAPI/System/Video/inputs/channels/1/motionDetection, enabled=true) --
this doesn't do its own frame analysis, it just listens for what the
camera's DSP already decided was motion.

Pulls credentials from DOOR_CAM_RTSP_URL (shared with camera_bridge's own
.env) rather than duplicating the password in a second env var.
"""
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.auth import HTTPDigestAuth

import config

CAM_URL = urlparse(config.DOOR_CAM_RTSP_URL)
CAM_HOST = CAM_URL.hostname
CAM_USER = CAM_URL.username or "admin"
CAM_PASS = CAM_URL.password or ""

ALERT_STREAM_URL = f"http://{CAM_HOST}/ISAPI/Event/notification/alertStream"
SNAPSHOT_URL = f"http://{CAM_HOST}/ISAPI/Streaming/channels/101/picture"

CLIP_DIR = Path(os.environ.get("MOTION_CLIP_DIR", str(Path.home() / "camera_clips" / "door")))
CLIP_SECONDS = int(os.environ.get("MOTION_CLIP_SECONDS", "20"))
RETENTION_DAYS = int(os.environ.get("MOTION_RETENTION_DAYS", "60"))
# Don't refire while the same motion event is still being reported as active,
# or on the next VMD blip a few seconds later -- one notification per episode.
DEBOUNCE_SECONDS = int(os.environ.get("MOTION_DEBOUNCE_SECONDS", "60"))

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def _ensure_clip_dir() -> Path:
    day_dir = CLIP_DIR / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def _prune_old_clips() -> None:
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    if not CLIP_DIR.exists():
        return
    for day_dir in CLIP_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d")
        except ValueError:
            continue
        if day < cutoff:
            for f in day_dir.glob("*"):
                f.unlink(missing_ok=True)
            day_dir.rmdir()


def _grab_clip(out_path: Path) -> bool:
    """ffmpeg pull of CLIP_SECONDS starting now -- not a pre-roll buffer, so
    it'll miss whatever happened in the second or so before the camera's own
    detection triggered + the HTTP round trip. Good enough for "something's
    at the door," not frame-perfect forensics."""
    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", config.DOOR_CAM_RTSP_URL,
        "-t", str(CLIP_SECONDS),
        "-c", "copy",
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd, timeout=CLIP_SECONDS + 15, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[motion_watcher] clip capture failed: {exc}")
        return False


def _grab_snapshot() -> bytes | None:
    try:
        resp = requests.get(SNAPSHOT_URL, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=10)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        print(f"[motion_watcher] snapshot fetch failed: {exc}")
        return None


def _send_alert() -> None:
    if not NTFY_TOPIC:
        print("[motion_watcher] NTFY_TOPIC not set, skipping push")
        return
    snapshot = _grab_snapshot()
    headers = {"Title": "Motion at the door", "Priority": "default", "Tags": "door,camera"}
    try:
        if snapshot:
            headers["Filename"] = "snapshot.jpg"
            requests.put(NTFY_URL, data=snapshot, headers=headers, timeout=10)
        else:
            requests.post(NTFY_URL, data="Motion detected at the door", headers=headers, timeout=10)
    except requests.RequestException as exc:
        print(f"[motion_watcher] ntfy push failed: {exc}")


def _handle_motion_event() -> None:
    print("[motion_watcher] motion event -- capturing clip + alert")
    # Fire the push immediately in the background rather than waiting on the
    # ~20s clip capture -- you want to know NOW, not after ffmpeg finishes.
    threading.Thread(target=_send_alert, daemon=True).start()
    day_dir = _ensure_clip_dir()
    clip_path = day_dir / f"{datetime.now().strftime('%H-%M-%S')}.mp4"
    if not _grab_clip(clip_path) and clip_path.exists():
        clip_path.unlink(missing_ok=True)
    _prune_old_clips()


_EVENT_RE = re.compile(rb"<eventType>(?P<type>\w+)</eventType>\s*<eventState>(?P<state>\w+)</eventState>")


def watch_forever() -> None:
    last_trigger = 0.0
    while True:
        try:
            resp = requests.get(
                ALERT_STREAM_URL,
                auth=HTTPDigestAuth(CAM_USER, CAM_PASS),
                stream=True,
                timeout=(10, 90),
            )
            resp.raise_for_status()
            buf = b""
            for chunk in resp.iter_content(chunk_size=1024):
                if not chunk:
                    continue
                buf += chunk
                while b"--boundary" in buf:
                    frag, _, buf = buf.partition(b"--boundary")
                    m = _EVENT_RE.search(frag)
                    if m and m.group("type") == b"VMD" and m.group("state") == b"active":
                        now = time.time()
                        if now - last_trigger >= DEBOUNCE_SECONDS:
                            last_trigger = now
                            _handle_motion_event()
                if len(buf) > 65536:  # safety net against unbounded growth if a boundary never shows up
                    buf = buf[-4096:]
        except requests.RequestException as exc:
            print(f"[motion_watcher] alertStream connection dropped: {exc} -- reconnecting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    if not CAM_HOST:
        raise SystemExit("DOOR_CAM_RTSP_URL isn't set -- motion_watcher needs it for the camera's host/creds")
    _prune_old_clips()
    watch_forever()
