"""Bridge into D.A.V.E.'s real, already-running API server
(jarvis_v2/api_server.py) -- proxies into it over HTTP instead of
importing query.py cold into a second process. api_server.py already owns
the Chroma/Ollama connections, session history, and interaction logging;
daveweb has no business re-implementing any of that. The only thing
daveweb adds on top is the outer password+MFA wall needed before this is
reachable from the open internet instead of just your tailnet.
"""
import threading

import requests

import config

_session_token = None
_token_lock = threading.Lock()


class _LoginFailed(Exception):
    pass


def _login() -> str:
    resp = requests.post(
        f"{config.DAVE_API_BASE}/login",
        data={"password": config.DAVE_GUI_PASSWORD},
        timeout=10,
    )
    if resp.status_code == 401:
        raise _LoginFailed(
            "api_server.py rejected DAVE_GUI_PASSWORD -- it doesn't match "
            "whatever password api_server.py is actually running with. "
            "Check daveweb's .env against api_server.py's own config/env."
        )
    resp.raise_for_status()
    return resp.json()["token"]


def _get_token(force_refresh: bool = False) -> str:
    global _session_token
    with _token_lock:
        if _session_token is None or force_refresh:
            _session_token = _login()
        return _session_token


def ask_dave(question: str, use_web: bool = True, cite_sources: bool = True) -> str:
    if not config.DAVE_GUI_PASSWORD:
        return (
            "DAVE_GUI_PASSWORD isn't set in daveweb's .env -- add the same "
            "password api_server.py checks against (whatever you set when "
            "you ran it / whatever's in its systemd EnvironmentFile)."
        )

    payload = {"question": question, "use_web": use_web, "cite_sources": cite_sources}

    try:
        token = _get_token()
        resp = requests.post(
            f"{config.DAVE_API_BASE}/ask",
            json=payload,
            headers={"X-Dave-Session": token},
            timeout=120,
        )
        if resp.status_code == 401:
            # api_server.py's SESSIONS set is in-memory and clears on its
            # own restart -- a stale token here just means it restarted
            # since we last logged in. Re-login once and retry.
            token = _get_token(force_refresh=True)
            resp = requests.post(
                f"{config.DAVE_API_BASE}/ask",
                json=payload,
                headers={"X-Dave-Session": token},
                timeout=120,
            )
    except _LoginFailed as exc:
        return str(exc)

    except requests.ConnectionError:
        return (
            f"Couldn't reach D.A.V.E.'s API server at {config.DAVE_API_BASE} "
            "-- is api_server.py running? (systemctl status / check it "
            "wasn't the thing squatting on this port)."
        )
    except requests.Timeout:
        return "D.A.V.E.'s API server didn't respond in time (120s). It may be stuck on a long generation."
    except requests.RequestException as exc:
        return f"Request to D.A.V.E.'s API server failed: {exc}"

    if resp.status_code != 200:
        return f"D.A.V.E.'s API server returned {resp.status_code}: {resp.text[:300]}"

    return resp.json().get("answer") or "(no response)"


def ask_vision(camera: str, question: str | None = None) -> str:
    """'Ask D.A.V.E. what it sees' -- proxies into api_server.py's
    /vision/ask, which fetches a fresh snapshot from camera_bridge itself
    and runs it through Claude's vision API. daveweb never touches the
    image bytes for this path."""
    if not config.DAVE_GUI_PASSWORD:
        return (
            "DAVE_GUI_PASSWORD isn't set in daveweb's .env -- add the same "
            "password api_server.py checks against."
        )

    payload = {"camera": camera, "question": question}

    try:
        token = _get_token()
        resp = requests.post(
            f"{config.DAVE_API_BASE}/vision/ask",
            json=payload,
            headers={"X-Dave-Session": token},
            timeout=30,
        )
        if resp.status_code == 401:
            token = _get_token(force_refresh=True)
            resp = requests.post(
                f"{config.DAVE_API_BASE}/vision/ask",
                json=payload,
                headers={"X-Dave-Session": token},
                timeout=30,
            )
    except _LoginFailed as exc:
        return str(exc)
    except requests.ConnectionError:
        return (
            f"Couldn't reach D.A.V.E.'s API server at {config.DAVE_API_BASE} "
            "-- is api_server.py running?"
        )
    except requests.Timeout:
        return "D.A.V.E.'s API server didn't respond in time (30s)."
    except requests.RequestException as exc:
        return f"Request to D.A.V.E.'s API server failed: {exc}"

    if resp.status_code != 200:
        return f"D.A.V.E.'s API server returned {resp.status_code}: {resp.text[:300]}"

    return resp.json().get("answer") or "(no response)"


def generate_mitigation(cve_id: str, description: str, matched_asset: str,
                         cvss_score=None, required_action: str = "") -> str:
    """Ask D.A.V.E. (the real one, via /ask) to write mitigation/patch
    guidance for one specific finding. This is explicitly AI-generated --
    the caller is responsible for labeling it as such in the UI, same
    principle as the Cyber Defense Daily AI-brief plan: useful output,
    never presented as if a human wrote or verified it.
    """
    severity_line = f"CVSS score: {cvss_score}\n" if cvss_score else ""
    kev_line = f"CISA's required action: {required_action}\n" if required_action else ""

    prompt = (
        f"One of my homelab devices/services matches a vulnerability THREATWATCH just flagged. "
        f"Give me practical mitigation and patch guidance for MY situation specifically, not a "
        f"generic writeup.\n\n"
        f"CVE: {cve_id}\n"
        f"Affected asset in my homelab: {matched_asset}\n"
        f"{severity_line}"
        f"{kev_line}"
        f"Description: {description}\n\n"
        f"Answer in this shape: (1) is this actually exploitable in my setup given what you know "
        f"about my network, (2) the concrete patch/upgrade/config change to apply, (3) a stopgap "
        f"mitigation if I can't patch right now (firewall rule, disable a feature, isolate the "
        f"VLAN, etc.). Keep it tight -- I don't need padding, I need to know what to actually do."
    )

    return ask_dave(prompt, use_web=True, cite_sources=True)


def generate_daily_brief(prompt: str) -> str:
    """Thin wrapper so daily_brief generation goes through the same
    ask_dave() path (and the same error handling) as everything else --
    no separate reason for this one to behave differently. use_web=False
    since the brief is meant to summarize THREATWATCH's own already-pulled
    data, not go fetch more."""
    return ask_dave(prompt, use_web=False, cite_sources=False)
