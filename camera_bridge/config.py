"""All settings via env vars, same philosophy as daveweb/config.py and
threatwatch/config.py -- no secrets hardcoded, nothing to accidentally
commit.

This service is meant to listen on the Pi 5's Tailscale interface only
(or plain LAN if you'd rather keep cameras off the tailnet entirely) --
it is NEVER meant to be reachable from the public internet. daveweb is
the only thing that talks to it, and daveweb is what sits behind the
password+MFA wall. CAMERA_BRIDGE_TOKEN is a second layer on top of that
network boundary, not a replacement for it -- camera control (pan/tilt)
and a live view of your home are worth the extra header check even on a
"trusted" network.
"""
import os

# Shared secret daveweb sends as X-Camera-Token on every request. Generate
# one with: python3 -c "import secrets; print(secrets.token_hex(32))"
CAMERA_BRIDGE_TOKEN = os.environ.get("CAMERA_BRIDGE_TOKEN", "")

# 0.0.0.0 is fine here specifically because this is meant to run on the
# Pi's Tailscale/LAN-only interface with nothing else exposing it further --
# unlike daveweb and api_server.py, there's no Funnel/public step downstream
# of this one. Still worth binding to the tailscale0 interface IP directly
# instead of 0.0.0.0 if your Pi is also on an untrusted LAN segment.
HOST = os.environ.get("CAMERA_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("CAMERA_BRIDGE_PORT", "8600"))

# Door cam: Hikvision-compatible PoE IP camera, read via RTSP/OpenCV's
# FFMPEG backend. This is what's actually installed (Oct 2026) -- set the
# full rtsp:// URL (credentials included) in .env as DOOR_CAM_RTSP_URL.
# If left blank, falls back to a plain USB webcam via OpenCV/V4L2 at
# DOOR_CAM_DEVICE instead (index 0 is usually right if it's the only USB
# cam on the Pi -- check with `ls /dev/video*` otherwise).
DOOR_CAM_RTSP_URL = os.environ.get("DOOR_CAM_RTSP_URL", "")
DOOR_CAM_DEVICE = int(os.environ.get("DOOR_CAM_DEVICE", "0"))

# Window cam: Pi Camera Module 3 on the CSI port, read via picamera2.
# camera_num is almost always 0 on a Pi with a single CSI camera attached;
# only matters if you ever add a second CSI cam.
WINDOW_CAM_CAMERA_NUM = int(os.environ.get("WINDOW_CAM_CAMERA_NUM", "0"))

# Capture resolution -- kept modest on purpose. This has to run comfortably
# on a Pi 5 alongside everything else sharing it, and daveweb re-serves
# these streams over Tailscale Funnel, so there's no reason to push 4K.
STREAM_WIDTH = int(os.environ.get("CAMERA_STREAM_WIDTH", "1280"))
STREAM_HEIGHT = int(os.environ.get("CAMERA_STREAM_HEIGHT", "720"))
JPEG_QUALITY = int(os.environ.get("CAMERA_JPEG_QUALITY", "80"))

# Door cam is mounted upside-down relative to how its sensor reads frames --
# rotate 180 degrees before serving. If you ever remount it right-side up,
# set this to false.
DOOR_CAM_ROTATE_180 = os.environ.get("DOOR_CAM_ROTATE_180", "true").lower() not in (
    "false", "0", "no",
)

# Pan-Tilt HAT (Pimoroni) travel limits in degrees, centered on 0. The HAT
# itself usually tolerates a bit more than this but these are sane
# "won't stare at the ceiling or grind the servo" defaults -- adjust once
# you've actually seen how your window mount behaves at the extremes.
PAN_MIN, PAN_MAX = -80, 80
TILT_MIN, TILT_MAX = -30, 30
