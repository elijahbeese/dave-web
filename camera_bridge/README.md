# camera_bridge

Runs on the Pi 5. Owns both cameras and the Pan-Tilt HAT directly, and is
the only thing that does -- daveweb and D.A.V.E.'s core never touch camera
hardware themselves, they talk to this service over HTTP instead.

- **Door cam** -- Hikvision-compatible PoE IP turret camera, not USB
  despite this doc's original plan. Powered via a PoE injector, bridged
  wirelessly to the LAN through a WiFi extender in bridge mode (no direct
  ethernet run to the door), read over RTSP -- see `DOOR_CAM_RTSP_URL`
  below. The camera also exposes Hikvision's ISAPI over plain HTTP on
  itself, which `motion_watcher.py` uses for motion detection (see
  below) -- separate from camera_bridge's own stream/snapshot endpoints.
- **Window cam** -- Pi Camera Module 3 on the CSI ribbon, mounted on the
  Pimoroni Pan-Tilt HAT, same Pi.

## Physical setup

1. **Pi Camera Module 3 + Pan-Tilt HAT, in this order** (power off the Pi
   first):
   - Mount the Camera Module 3 onto the Pan-Tilt HAT's pan-tilt bracket per
     Pimoroni's bracket instructions (two small screws, camera board faces
     out).
   - Connect the camera's ribbon cable to the Pi 5's **camera** CSI port
     (there are two CSI/DSI-capable ports on a Pi 5 -- use the one marked
     for camera, not the display one). Blue tab on the ribbon faces the
     USB-C power port; metal contacts face the HDMI ports. The clip on the
     Pi 5's connector lifts up, not slides out -- lift gently, seat the
     ribbon, press the clip back down.
   - Stack the Pan-Tilt HAT onto the Pi 5's 40-pin GPIO header. The HAT
     talks over I2C, which is unaffected by the Pi 5's GPIO chip change
     that broke some older RPi.GPIO-dependent HATs -- `pantilthat` only
     needs I2C and should work fine.
   - Route the camera ribbon so it isn't pinched when the HAT seats flush
     on the header.

2. **Door cam (PoE IP camera)** -- no physical connection to the Pi at
   all. Power it via a PoE injector, confirm it's reachable on your LAN
   (its own web portal, usually plain HTTP on its IP), and set its admin
   credentials there. The Pi doesn't need to be anywhere near it --
   camera_bridge pulls the stream over the network via RTSP.

   If ethernet isn't practical to run to the camera's mounting location
   (ours wasn't), bridge it over WiFi with an extender in AP/bridge mode
   between the camera and your router/switch instead of running cable --
   the camera itself has no WiFi radio, it just needs *an* ethernet port
   to plug into, which the extender provides wirelessly.

   Set `DOOR_CAM_RTSP_URL` in `.env` to the camera's full RTSP URL
   (credentials embedded, e.g.
   `rtsp://admin:yourpassword@<camera-ip>:554/Streaming/Channels/101`).
   `DOOR_CAM_DEVICE`/USB fallback only matters if you leave this blank.

3. **Enable the camera + I2C interfaces**:
   ```bash
   sudo raspi-config
   # Interface Options -> Camera -> Enable (if not already, Bookworm+ usually auto-detects)
   # Interface Options -> I2C -> Enable
   sudo reboot
   ```
   After reboot, confirm the CSI camera is detected:
   ```bash
   libcamera-hello --list-cameras
   ```
   and the Pan-Tilt HAT shows up on the I2C bus (default address `0x15`):
   ```bash
   sudo apt install -y i2c-tools
   i2cdetect -y 1
   ```

## Software setup

```bash
cd ~/camera_bridge

# picamera2 comes from apt, built against this OS's libcamera stack --
# pip install picamera2 will NOT work correctly. Install it system-wide
# first, then create the venv with --system-site-packages so the venv can
# still see it.
sudo apt install -y python3-picamera2 python3-opencv i2c-tools v4l-utils

python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# generate a token and put it in .env:
python3 -c "import secrets; print(secrets.token_hex(32))"
# put the SAME value in daveweb's .env as CAMERA_BRIDGE_TOKEN
```

Add your user to the groups that own the camera/I2C device nodes:
```bash
sudo usermod -aG video,i2c $USER
# log out and back in (or reboot) for this to take effect
```

## Smoke-test before installing as a service

```bash
set -a; source .env; set +a
python3 app.py
```

From another machine on the same tailnet/LAN:
```bash
curl -H "X-Camera-Token: <your token>" http://<pi-tailscale-ip>:8600/health
# {"door": "ok", "window": "ok"}
```
If one camera shows an error instead of "ok", that's your signal something
in setup above needs another look -- `/health` doesn't require either
camera to be working, so you'll see partial results if only one is wired
up right so far.

Visit `http://<pi-tailscale-ip>:8600/snapshot/door.jpg?` -- wait, snapshot
needs the header too, so test with curl instead:
```bash
curl -H "X-Camera-Token: <your token>" http://<pi-tailscale-ip>:8600/snapshot/door.jpg -o door.jpg
curl -H "X-Camera-Token: <your token>" http://<pi-tailscale-ip>:8600/snapshot/window.jpg -o window.jpg
```
Pull `door.jpg`/`window.jpg` off the Pi and actually look at them before
moving on -- confirms both cameras are not just "detected" but genuinely
producing a real image.

Test pan/tilt:
```bash
curl -X POST -H "X-Camera-Token: <your token>" -H "Content-Type: application/json" \
  -d '{"pan": 20, "tilt": -10}' http://<pi-tailscale-ip>:8600/pantilt/move
```
Watch the camera actually move. If nothing happens, double check
`i2cdetect -y 1` shows the HAT and that your user is in the `i2c` group.

## Install as a service

```bash
sudo cp deploy/camera-bridge.service /etc/systemd/system/
# edit it if your username/paths differ from /home/youruser
sudo systemctl daemon-reload
sudo systemctl enable --now camera-bridge.service
systemctl status camera-bridge.service
```

## Security notes

- This binds `0.0.0.0:8600` by default, with no further exposure step --
  unlike daveweb and api_server.py, nothing puts this behind Tailscale
  Funnel. Keep it that way; it should only ever be reachable from your
  tailnet/LAN, never the public internet. If the Pi has a public interface
  for any reason, bind to the tailscale0 interface's specific IP instead
  of `0.0.0.0`.
- `CAMERA_BRIDGE_TOKEN` is the only thing stopping anything else on your
  LAN/tailnet from watching your cameras or moving the window cam. Treat
  it like a password -- it's gitignored in `.env`, keep it that way.
- Pan/tilt angles are clamped server-side (`config.PAN_MIN/MAX`,
  `TILT_MIN/MAX`) regardless of what's requested, so a bad value from
  daveweb (or a bug in it) can't drive the servo into its mechanical
  limit repeatedly.

## Motion detection & clip storage

`motion_watcher.py` runs as its own systemd service (`motion-watcher.service`),
separate from this one -- it holds a long-lived connection to the door
camera's own ISAPI alertStream and reacts when the camera's own DSP
flags motion (not a frame-polling/YOLO setup -- the camera does the
detection, this just listens).

Requires motion detection to actually be turned on in the camera's own
config first (off by default):
```bash
curl -s --digest -u admin:'yourpassword' -X PUT -H "Content-Type: application/xml" \
  --data-binary @motion_detection_enable.xml \
  http://<camera-ip>/ISAPI/System/Video/inputs/channels/1/motionDetection
```
(GET that same URL first to see the current config and flip `<enabled>`
to `true` while keeping your existing sensitivity/grid settings.)

On a motion event: grabs a short RTSP clip to local disk
(`~/camera_clips/<camera>/<date>/`, `MOTION_CLIP_DIR` to override),
prunes anything past `MOTION_RETENTION_DAYS` (default 60), and pushes an
ntfy.sh notification with a snapshot (`NTFY_TOPIC` in `.env`).

camera_bridge itself exposes the stored clips for daveweb to list/play:
```
GET /clips/{camera}                      -- JSON list, newest first
GET /clips/{camera}/{date}/{filename}     -- the actual clip (video/mp4)
```
both token-gated the same as every other endpoint here.

## Known gaps

- Single viewer assumption isn't enforced -- multiple browser tabs
  hitting `/stream/door.mjpg` simultaneously all share the same
  background-captured frames (that's the point, no extra camera load),
  but there's no limit on how many can connect at once.
