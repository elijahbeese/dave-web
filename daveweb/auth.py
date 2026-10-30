"""Single-user auth: password + TOTP MFA. No accounts table, no signup flow --
this app has exactly one user (you), so a .env-stored hash + secret is the
whole identity system. Deliberately boring/simple over "proper" multi-user
auth, because multi-user auth for a one-person dashboard is just more attack
surface for no benefit.
"""
import time
from collections import defaultdict
from functools import wraps

import pyotp
from flask import session, redirect, url_for, request
from werkzeug.security import check_password_hash

import config

# Very small in-memory lockout. Resets on process restart -- fine for a
# single-operator app behind Tailscale Funnel, not a substitute for real
# rate-limiting if this ever gets more exposed than "me, from my phone."
_FAILED_ATTEMPTS = defaultdict(list)  # ip -> [timestamps]
_LOCKOUT_WINDOW_SEC = 15 * 60
_LOCKOUT_MAX_ATTEMPTS = 5


def _client_ip() -> str:
    # Tailscale Funnel proxies through tailscaled locally; X-Forwarded-For
    # isn't trustworthy here the way it would be behind a real load
    # balancer, but request.remote_addr is -- it's always the funnel's
    # local hop, which is good enough for a per-process lockout counter.
    return request.remote_addr or "unknown"


def is_locked_out(ip: str | None = None) -> bool:
    ip = ip or _client_ip()
    now = time.time()
    attempts = [t for t in _FAILED_ATTEMPTS[ip] if now - t < _LOCKOUT_WINDOW_SEC]
    _FAILED_ATTEMPTS[ip] = attempts
    return len(attempts) >= _LOCKOUT_MAX_ATTEMPTS


def record_failed_attempt(ip: str | None = None) -> None:
    ip = ip or _client_ip()
    _FAILED_ATTEMPTS[ip].append(time.time())


def clear_failed_attempts(ip: str | None = None) -> None:
    ip = ip or _client_ip()
    _FAILED_ATTEMPTS.pop(ip, None)


def check_password(password: str) -> bool:
    return check_password_hash(config.DAVEWEB_PASSWORD_HASH, password)


def check_totp(code: str) -> bool:
    totp = pyotp.TOTP(config.DAVEWEB_TOTP_SECRET)
    # valid_window=1 forgives +/-30s clock drift between phone and server,
    # which is the single most common reason MFA "doesn't work."
    return totp.verify(code, valid_window=1)


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped
