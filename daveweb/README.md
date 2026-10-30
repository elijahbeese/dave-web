# daveweb — Phase A of the elijahbeese.com integration

Password + TOTP MFA wall, THREATWATCH dashboard, and a chat UI onto D.A.V.E.'s
real pipeline. Runs on yourhost, exposed to the internet via **Tailscale
Funnel** — not port forwarding, not a reverse proxy you have to maintain certs
for. elijahbeese.com itself is never touched: WordPress just gets a plain nav
link pointing at wherever Funnel publishes this.

This is deliberately a separate app, separate codebase, separate login from
any other project. Don't reuse this login, this subdomain, or this code for
anything else — keep them apart on purpose.

## What this is (and isn't) — Phase A only

- ✅ Password + MFA login wall
- ✅ Dashboard: live THREATWATCH counts, last 24h brief, collector run health
- ✅ Chat: proxies into your real, already-running `jarvis_v2/api_server.py`
  (`/ask`), not a reimplementation -- same retrieval/web-search/THREATWATCH
  gating, same session history, same interaction logging it already does
- ❌ Not yet: D.A.V.E.-written news articles (Phase C), realtime patch
  recommendations (Phase E), any auto-patching (Phase F — and per your own
  "no Ultron" rule, that phase will always require your approval per action,
  never run unsupervised)

## Setup on yourhost

```bash
# 1. Get the code onto the box (scp this folder over, same as threatwatch was)
cd ~/daveweb

# 2. Install deps. Doesn't need ollama/chromadb (that's api_server.py's job,
#    not daveweb's) -- jarvis_v2's venv already has everything else daveweb
#    needs plus requests, so reusing it is fine, but daveweb could just as
#    easily get its own lightweight venv if you'd rather keep this decoupled.
source ~/jarvis_v2/venv/bin/activate
pip install -r requirements.txt

# 3. Generate your login credentials
python3 setup_mfa.py
# -> prints DAVEWEB_SECRET_KEY / DAVEWEB_PASSWORD_HASH / DAVEWEB_TOTP_SECRET
# -> scan mfa_qr.png with your authenticator app, then: rm mfa_qr.png

cp .env.example .env
# paste the three generated values in, AND set DAVE_GUI_PASSWORD to the
# same password api_server.py itself checks against -- daveweb logs into
# it on your behalf using this.

# 4. Smoke-test it locally first
set -a; source .env; set +a
python3 app.py
# visit http://127.0.0.1:8421 from ON the box (curl, or SSH -L forward) --
# confirm login + MFA + dashboard + a chat message (which round-trips
# through api_server.py's own /ask) all work before going anywhere near
# Funnel. api_server.py must already be running for chat to work --
# it should be, since it's set up to survive reboots.
```

Once that works, install it as a real service:

```bash
sudo cp deploy/daveweb.service /etc/systemd/system/
# edit it if your paths/user differ from /home/youruser
sudo systemctl daemon-reload
sudo systemctl enable --now daveweb.service
systemctl status daveweb.service
```

## Exposing it — Tailscale Funnel

Funnel publishes a local port to the public internet on your tailnet's own
`*.ts.net` HTTPS hostname, with TLS handled by Tailscale — no certs to
manage, no port forwarding, no fighting your Xfinity double-NAT.

```bash
# enable Funnel for this node once (first time only, needs tailnet admin approval
# in the Tailscale admin console -- it'll print a link if so)
sudo tailscale funnel 443 on

# tell Funnel to forward public :443 traffic to daveweb's local port
# (8421 -- NOT 8420, that's api_server.py's port and should stay internal
# now that daveweb is the thing facing the internet)
sudo tailscale serve --bg --https 443 http://127.0.0.1:8421
sudo tailscale funnel --bg 443
```

One more thing worth doing once this is live: api_server.py currently binds
`0.0.0.0:8420` (not `127.0.0.1` like its own log message claims), so it's
reachable from anything on your LAN/VLAN right now, gated only by its one
static password. Once daveweb/Funnel is your actual public entry point,
consider changing that bind to `127.0.0.1` too, since nothing should be
hitting it directly anymore except daveweb on the same box.

Run `tailscale funnel status` to get your public URL — it'll look like
`https://yourhost.<your-tailnet-name>.ts.net`. That's the link you put in
WordPress's nav (just a plain `<a href>` in a Custom HTML block, same pattern
your "Field Log" theme already uses for everything else — no plugin, no PHP
talking to yourhost).

## Security notes

- `DAVEWEB_HOST` stays `127.0.0.1` always. Funnel is what makes this
  reachable from outside — the app itself never binds to a public interface.
- Login lockout (5 failed attempts / 15 min) is in-memory, per gunicorn
  worker, reset on restart. Fine for a single-operator app; don't mistake it
  for real rate-limiting if this ever gets linked from somewhere public.
- Session cookies are httponly, SameSite=Lax, and expire after 12h
  (`DAVEWEB_SESSION_HOURS`).
- `.env` and `mfa_qr.png` are gitignored. If you ever `git init` this
  directory, double check they never get committed.

## Known gaps to revisit

- Chat is request/response, not streaming — `api_server.py` already has
  `/ask/stream` (SSE) and daveweb's chat page could consume that instead
  of `/ask` for faster-feeling responses, just didn't want to add that
  complexity before the basic wall was proven out.
- No CSRF token on the login form yet (single-user, low-value CSRF target,
  but worth adding before Phase D expands this).
- `dave_api.py`'s session token to api_server.py lives in daveweb's own
  process memory and never expires on daveweb's side — it only gets
  refreshed if api_server.py itself restarts and rejects it with a 401.
  Fine for how this is used today.
