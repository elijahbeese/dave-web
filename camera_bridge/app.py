"""camera_bridge -- runs on the Pi 5, talks directly to the two cameras and
the Pan-Tilt HAT, and is the ONLY thing on the box that does. Nothing else
(daveweb, jarvis_v2) touches /dev/video0 or the CSI camera or the HAT's I2C
bus directly -- they all go through this service's HTTP API instead, same
"one process owns the hardware" philosophy daveweb already uses for
Ollama/Chroma (via api_server.py) and now extends to cameras.

Both cameras are read continuously by a background thread into a shared
"latest frame" buffer, rather than opening the device fresh on every HTTP
request -- V4L2/picamera2 devices don't like being opened by multiple
readers at once, and re-opening per-request would also make every browser
tab fight over exclusive access to the camera.

Run directly for testing:
    uvicorn app:app --host 0.0.0.0 --port 8600
Production run is via systemd, see deploy/camera-bridge.service.
"""
import os
import threading
import time

import cv2
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path

import config
import pantilt_state

# OpenCV's FFmpeg backend defaults to UDP for RTSP, which drops packets
# easily on a stream that's hopping through a WiFi extender bridge before
# it reaches the Pi -- shows up as "error while decoding MB ..." spam and
# corrupted frames. Forcing TCP trades a little latency for retransmitting
# lost packets instead of just corrupting the frame. Must be set before
# the first cv2.VideoCapture(..., cv2.CAP_FFMPEG) call.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

app = FastAPI(title="camera_bridge")


def _check_token(x_camera_token: str = Header(default="")) -> None:
    if not config.CAMERA_BRIDGE_TOKEN:
        raise HTTPException(
            500,
            "CAMERA_BRIDGE_TOKEN isn't set in camera_bridge's .env -- generate one "
            "(python3 -c \"import secrets; print(secrets.token_hex(32))\") and set "
            "it here AND in daveweb's .env before this will accept any requests.",
        )
    if x_camera_token != config.CAMERA_BRIDGE_TOKEN:
        raise HTTPException(401, "bad or missing X-Camera-Token")


class _FrameSource:
    """Shared base for both cameras -- a background thread keeps writing the
    newest JPEG into self._frame, every read just grabs whatever's there.
    A reader that's slow (or a dropped frame) never blocks or stalls
    capture; it just sees the same frame twice."""

    def __init__(self, name: str):
        self.name = name
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._started = False
        self._error: str | None = None

    def latest(self) -> bytes:
        with self._lock:
            if self._error:
                raise HTTPException(503, f"{self.name} camera unavailable: {self._error}")
            if self._frame is None:
                raise HTTPException(503, f"{self.name} camera hasn't produced a frame yet")
            return self._frame

    def _set_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self._frame = jpeg_bytes
            self._error = None

    def _set_error(self, msg: str) -> None:
        with self._lock:
            self._error = msg

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self) -> None:
        raise NotImplementedError


class _DoorCam(_FrameSource):
    """Door cam. RTSP (Hikvision-compatible PoE IP camera) if
    DOOR_CAM_RTSP_URL is set -- that's what's actually installed at the
    door. Falls back to a plain USB webcam via OpenCV/V4L2 otherwise."""

    def _capture_loop(self) -> None:
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY]
        rtsp_url = config.DOOR_CAM_RTSP_URL
        label = "RTSP door cam" if rtsp_url else f"/dev/video{config.DOOR_CAM_DEVICE}"
        while True:
            if rtsp_url:
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                # Keep only the newest frame in OpenCV's internal queue --
                # without this, a reader that's ever a beat slow lets RTSP
                # frames pile up and the stream drifts further and further
                # behind real time the longer it runs.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            else:
                cap = cv2.VideoCapture(config.DOOR_CAM_DEVICE)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.STREAM_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.STREAM_HEIGHT)
            if not cap.isOpened():
                self._set_error(f"couldn't open {label}")
                time.sleep(5)
                continue
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        self._set_error("read failed, reopening device")
                        break
                    if rtsp_url and config.DOOR_CAM_ROTATE_180:
                        frame = cv2.rotate(frame, cv2.ROTATE_180)
                    # The IP camera's native res (e.g. 2560x1440) is bigger
                    # than daveweb needs to re-serve over Funnel -- resize
                    # down to the configured stream size, same as the other
                    # sources already produce.
                    h, w = frame.shape[:2]
                    if (w, h) != (config.STREAM_WIDTH, config.STREAM_HEIGHT):
                        frame = cv2.resize(frame, (config.STREAM_WIDTH, config.STREAM_HEIGHT))
                    ok, buf = cv2.imencode(".jpg", frame, encode_params)
                    if ok:
                        self._set_frame(buf.tobytes())
                    time.sleep(1 / 15)  # ~15fps -- plenty for a security feed, easy on the Pi
            finally:
                cap.release()
            time.sleep(2)  # brief pause before trying to reopen a source that just failed


class _WindowCam(_FrameSource):
    """Pi Camera Module 3 on the CSI port via picamera2.

    Imported lazily inside the thread (not at module load) so that
    camera_bridge can still start up and serve the door cam even if
    picamera2 isn't installed yet or the CSI cam isn't plugged in -- one
    broken camera shouldn't take the other one down with it.
    """

    def _capture_loop(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError:
            self._set_error(
                "picamera2 not installed -- on the Pi, this comes from apt "
                "(python3-picamera2), not pip. See camera_bridge/README.md."
            )
            return

        try:
            picam2 = Picamera2(camera_num=config.WINDOW_CAM_CAMERA_NUM)
            cfg = picam2.create_video_configuration(
                main={"size": (config.STREAM_WIDTH, config.STREAM_HEIGHT), "format": "RGB888"}
            )
            picam2.configure(cfg)
            picam2.start()
        except Exception as exc:  # noqa: BLE001 -- report it, keep the door cam alive
            self._set_error(f"failed to start: {exc}")
            return

        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY]
        try:
            while True:
                frame = picam2.capture_array()  # RGB888
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                ok, buf = cv2.imencode(".jpg", frame_bgr, encode_params)
                if ok:
                    self._set_frame(buf.tobytes())
                time.sleep(1 / 15)
        finally:
            picam2.stop()


door_cam = _DoorCam("door")
window_cam = _WindowCam("window")
_CAMS = {"door": door_cam, "window": window_cam}


@app.on_event("startup")
def _start_capture_threads():
    door_cam.start()
    window_cam.start()


def _mjpeg_generator(cam: _FrameSource):
    boundary = b"--frame"
    while True:
        frame = cam.latest()
        yield (
            boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(1 / 15)


@app.get("/health")
def health():
    return {
        "door": "ok" if door_cam._frame is not None else (door_cam._error or "starting"),
        "window": "ok" if window_cam._frame is not None else (window_cam._error or "starting"),
    }


@app.get("/stream/{camera}.mjpg")
def stream(camera: str, x_camera_token: str = Header(default="")):
    _check_token(x_camera_token)
    cam = _CAMS.get(camera)
    if not cam:
        raise HTTPException(404, "camera must be 'door' or 'window'")
    return StreamingResponse(
        _mjpeg_generator(cam), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/snapshot/{camera}.jpg")
def snapshot(camera: str, x_camera_token: str = Header(default="")):
    _check_token(x_camera_token)
    cam = _CAMS.get(camera)
    if not cam:
        raise HTTPException(404, "camera must be 'door' or 'window'")
    return Response(content=cam.latest(), media_type="image/jpeg")


@app.get("/pantilt/status")
def pantilt_status(x_camera_token: str = Header(default="")):
    _check_token(x_camera_token)
    return pantilt_state.get()


@app.post("/pantilt/move")
def pantilt_move(body: dict, x_camera_token: str = Header(default="")):
    """body: {"pan": int, "tilt": int} -- absolute angles in degrees,
    clamped to config.PAN_MIN/MAX and TILT_MIN/MAX regardless of what's
    sent, so a bad value from daveweb can't grind the servo into its
    mechanical limit."""
    _check_token(x_camera_token)
    try:
        from pantilthat import pan, tilt
    except ImportError:
        raise HTTPException(
            500,
            "pantilthat not installed -- pip install pantilthat (needs I2C enabled "
            "via raspi-config first). See camera_bridge/README.md.",
        )

    pan_deg = max(config.PAN_MIN, min(config.PAN_MAX, int(body.get("pan", 0))))
    tilt_deg = max(config.TILT_MIN, min(config.TILT_MAX, int(body.get("tilt", 0))))
    pan(pan_deg)
    tilt(tilt_deg)
    pantilt_state.set(pan_deg, tilt_deg)
    return pantilt_state.get()



CLIPS_ROOT = Path.home() / "camera_clips"
_VALID_CLIP_CAMERAS = ("door", "window")


def _camera_clip_dir(camera: str) -> Path:
    return CLIPS_ROOT / camera


@app.get("/clips/{camera}")
def list_clips(camera: str, x_camera_token: str = Header(default="")):
    """Lists motion-triggered clips for a camera, newest first. Written by
    motion_watcher.py -- door cam only has any right now, window cam has no
    motion detection pipeline yet, so this just comes back empty for it."""
    _check_token(x_camera_token)
    if camera not in _VALID_CLIP_CAMERAS:
        raise HTTPException(404, "camera must be 'door' or 'window'")
    cam_dir = _camera_clip_dir(camera)
    clips = []
    if cam_dir.exists():
        for day_dir in sorted(cam_dir.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            for f in sorted(day_dir.glob("*.mp4"), reverse=True):
                stat = f.stat()
                clips.append({
                    "date": day_dir.name,
                    "filename": f.name,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                })
    return {"camera": camera, "clips": clips}


@app.get("/clips/{camera}/{date}/{filename}")
def get_clip(camera: str, date: str, filename: str, x_camera_token: str = Header(default="")):
    _check_token(x_camera_token)
    if camera not in _VALID_CLIP_CAMERAS:
        raise HTTPException(404, "camera must be 'door' or 'window'")
    if "/" in date or "/" in filename or ".." in date or ".." in filename:
        raise HTTPException(400, "invalid path")
    cam_dir = _camera_clip_dir(camera)
    clip_path = (cam_dir / date / filename).resolve()
    if not str(clip_path).startswith(str(cam_dir.resolve())):
        raise HTTPException(400, "invalid path")
    if not clip_path.is_file():
        raise HTTPException(404, "clip not found")
    return FileResponse(clip_path, media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
