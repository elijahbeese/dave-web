"""D.A.V.E. Web -- password+MFA-walled dashboard, chatbot, and THREATWATCH
brief, meant to run on yourhost and be exposed via Tailscale Funnel.

Deliberately NOT embedded in WordPress: elijahbeese.com stays untouched
(no PHP plugin, no shared login with WordPress), this runs as its own app
on your own hardware, and the WordPress site just gets a plain nav link to
wherever Funnel publishes this. Keeps the attack surface, the codebase, and
your WordPress install's blast radius completely separate from D.A.V.E.
"""
import datetime as dt
import os
import sys

from flask import Flask, render_template, request, redirect, url_for, session, jsonify

import auth
import config
import dave_api

sys.path.insert(0, os.path.expanduser("~/threatwatch"))
from threatwatch import db as tw_db  # noqa: E402
from threatwatch.dave_bridge import get_recent_brief  # noqa: E402
from threatwatch.relevance import (  # noqa: E402
    get_relevant_findings, get_chart_data, get_related_articles, static_checklist,
)
from threatwatch import mitigations as tw_mitigations  # noqa: E402
from threatwatch import zerodays as tw_zerodays  # noqa: E402
from threatwatch.assets import icon_for, SEVERITY_ICONS  # noqa: E402
from threatwatch.daily_brief import (  # noqa: E402
    get_cached_brief, build_brief_prompt, build_news_brief_prompt, save_brief, today_str,
    build_suggestions_prompt, parse_suggestions, build_followup_prompt,
)

sys.path.insert(0, os.path.expanduser("~/jarvis_v2/pulse"))
from pulse import db as pulse_db  # noqa: E402
from pulse import config as pulse_config  # noqa: E402
from pulse.run_collectors import COLLECTORS as PULSE_COLLECTORS  # noqa: E402
from pulse_brief import build_pulse_brief_prompt  # noqa: E402

BRIEF_LABELS = {
    "threat": "THREATWATCH daily brief (your gear)",
    "news": "industry-wide cyber news brief",
    "pulse": "pulse brief (last 24h of Reddit/X)",
}

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,  # Funnel terminates TLS; cookies should never go out plain
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=config.SESSION_LIFETIME_HOURS),
)

if not config.FLASK_SECRET_KEY or not config.DAVEWEB_PASSWORD_HASH or not config.DAVEWEB_TOTP_SECRET:
    print(
        "WARNING: daveweb/.env is missing DAVEWEB_SECRET_KEY, "
        "DAVEWEB_PASSWORD_HASH, or DAVEWEB_TOTP_SECRET. Run setup_mfa.py "
        "and populate .env before exposing this via Funnel.",
        file=sys.stderr,
    )


@app.context_processor
def inject_severity_icons():
    return {"SEVERITY_ICONS": SEVERITY_ICONS}


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    error = None
    stage = request.form.get("stage", "password")

    if request.method == "POST":
        if auth.is_locked_out():
            error = "Too many failed attempts. Wait 15 minutes."
        elif stage == "password":
            password = request.form.get("password", "")
            if auth.check_password(password):
                session["pw_verified"] = True
                stage = "totp"
            else:
                auth.record_failed_attempt()
                error = "Wrong password."
        elif stage == "totp":
            if not session.get("pw_verified"):
                stage = "password"
                error = "Session expired, start over."
            else:
                code = request.form.get("totp_code", "")
                if auth.check_totp(code):
                    auth.clear_failed_attempts()
                    session.pop("pw_verified", None)
                    session["authenticated"] = True
                    session.permanent = True
                    next_url = request.args.get("next") or url_for("dashboard")
                    return redirect(next_url)
                else:
                    auth.record_failed_attempt()
                    error = "Wrong or expired code."
                    stage = "totp"

    return render_template("login.html", error=error, stage=stage)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _attach_finding_extras(f):
    """Shared enrichment for a finding dict -- used on both the dashboard
    cards and the per-CVE detail page so they never drift out of sync."""
    cached = tw_mitigations.get_cached(f["cve_id"])
    f["cached_guidance"] = cached["guidance"] if cached else None
    f["icon"] = icon_for(f["matched_asset"]) if f.get("matched_asset") else "🛡️"
    f["checklist"] = static_checklist(f["criticality"], f["is_kev"], f.get("zero_day"))
    f["related_articles"] = get_related_articles(
        [f["cve_id"], f.get("matched_asset", "").split(" (")[0]], limit=3
    )
    return f


@app.route("/")
@auth.login_required
def dashboard():
    with tw_db.connect() as conn:
        kev_count = conn.execute("SELECT COUNT(*) AS n FROM kev").fetchone()["n"]
        cve_count = conn.execute("SELECT COUNT(*) AS n FROM cves").fetchone()["n"]
        group_count = conn.execute("SELECT COUNT(*) AS n FROM attack_groups").fetchone()["n"]
        article_count = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        last_runs = conn.execute(
            "SELECT collector, status, records_in, run_at FROM ingestion_log "
            "ORDER BY run_at DESC LIMIT 10"
        ).fetchall()

    brief = get_recent_brief(hours=24)
    relevant = get_relevant_findings(limit=25)
    charts = get_chart_data(trend_days=30)
    daily_brief = get_cached_brief(today_str())

    with tw_db.connect() as conn:
        top_news = conn.execute(
            "SELECT source, title, link FROM articles ORDER BY fetched_at DESC LIMIT 5"
        ).fetchall()

    # Attach any already-cached D.A.V.E. guidance, the static checklist, an
    # icon, and related news -- all per-finding, all cheap (cache lookups /
    # in-memory dict lookups / small LIKE queries), so no reason to make the
    # detail page re-derive any of it differently from the dashboard cards.
    for f in relevant:
        _attach_finding_extras(f)

    return render_template(
        "dashboard.html",
        kev_count=kev_count,
        cve_count=cve_count,
        group_count=group_count,
        article_count=article_count,
        last_runs=last_runs,
        brief=brief,
        relevant=relevant,
        charts=charts,
        daily_brief=daily_brief,
        top_news=top_news,
    )


@app.route("/api/daily-brief/generate", methods=["POST"])
@auth.login_required
def api_generate_daily_brief():
    """Manual fallback for 'the scheduled timer hasn't run yet today' --
    the normal path is dave-daily-brief.timer calling generate_daily_brief.py
    once every morning, this just lets today's brief exist on demand instead
    of making you wait until tomorrow to see the feature work.

    Takes an optional ?kind=threat|news|pulse (default threat) -- 'threat' is
    the asset-matched brief, 'news' is the broader industry brief with
    per-story relevance notes, 'pulse' summarizes the last 24h of Reddit/X
    pulls. The first two run on the daily timer; 'pulse' is always
    on-demand only (no timer), matching the rest of pulse's design -- this
    endpoint only ever reads pulse's already-collected data, it never
    triggers a new pull itself.
    """
    kind = request.args.get("kind", "threat")
    if kind not in ("threat", "news", "pulse"):
        return jsonify({"error": "kind must be 'threat', 'news', or 'pulse'"}), 400
    existing = get_cached_brief(today_str(), kind=kind)
    if existing:
        return jsonify({
            "brief": existing["brief"], "cached": True,
            "suggestions": existing["suggestions"],
        })
    if kind == "threat":
        prompt = build_brief_prompt()
    elif kind == "news":
        prompt = build_news_brief_prompt()
    else:
        prompt = build_pulse_brief_prompt()
    brief_text = dave_api.generate_daily_brief(prompt)
    # One more small LLM call to pre-generate the brief-chat box's suggested
    # follow-up chips, cached alongside the brief itself so a page reload
    # doesn't have to regenerate them.
    suggestions_raw = dave_api.generate_daily_brief(build_suggestions_prompt(brief_text))
    suggestions = parse_suggestions(suggestions_raw)
    save_brief(brief_text, model="dave-api", kind=kind, suggestions=suggestions)
    return jsonify({"brief": brief_text, "cached": False, "suggestions": suggestions})


@app.route("/api/brief-chat/ask", methods=["POST"])
@auth.login_required
def api_brief_chat_ask():
    """Follow-up Q&A scoped to one already-generated brief -- the chat box
    under each brief panel (threat/news/pulse). Always re-sends the brief
    text as context (see build_followup_prompt's docstring for why) plus
    this session's own earlier turns on that same brief, so a chain of
    follow-ups stays coherent without the API server needing to track any
    of this itself.
    """
    body = request.json or {}
    kind = body.get("kind", "threat")
    if kind not in BRIEF_LABELS:
        return jsonify({"error": "kind must be 'threat', 'news', or 'pulse'"}), 400
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "empty question"}), 400
    history = body.get("history") or []

    cached = get_cached_brief(today_str(), kind=kind)
    if not cached:
        return jsonify({"error": "no brief generated yet today -- generate one first"}), 400

    prompt = build_followup_prompt(BRIEF_LABELS[kind], cached["brief"], question, history=history)
    answer = dave_api.ask_dave(prompt, use_web=True, cite_sources=True)
    return jsonify({"answer": answer})


@app.route("/finding/<cve_id>")
@auth.login_required
def finding_detail(cve_id):
    relevant = get_relevant_findings(limit=200)  # generous cap; this is a direct lookup, not a feed
    match = next((f for f in relevant if f["cve_id"] == cve_id), None)
    if not match:
        return render_template("finding_detail.html", finding=None, cve_id=cve_id), 404
    _attach_finding_extras(match)
    return render_template("finding_detail.html", finding=match, cve_id=cve_id)


@app.route("/zero-days")
@auth.login_required
def zero_days():
    findings = tw_zerodays.get_zero_days(limit=50)
    for f in findings:
        f["icon"] = icon_for(f["matched_asset"]) if f.get("matched_asset") else None
    return render_template("zero_days.html", findings=findings)


@app.route("/news")
@auth.login_required
def news():
    with tw_db.connect() as conn:
        rows = conn.execute(
            "SELECT source, title, link, summary, published FROM articles "
            "ORDER BY fetched_at DESC LIMIT 60"
        ).fetchall()
    articles = [dict(r) for r in rows]
    news_brief = get_cached_brief(today_str(), kind="news")
    return render_template("news.html", articles=articles, news_brief=news_brief)


@app.route("/chat")
@auth.login_required
def chat():
    return render_template("chat.html")


@app.route("/api/chat", methods=["POST"])
@auth.login_required
def api_chat():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "empty question"}), 400
    answer = dave_api.ask_dave(question)
    return jsonify({"answer": answer})


@app.route("/api/mitigate/<cve_id>", methods=["POST"])
@auth.login_required
def api_mitigate(cve_id):
    """Generate (or return cached) D.A.V.E.-written mitigation guidance for
    one finding. Deliberately on-demand/per-click, not automatic on every
    dashboard load -- each one is a real LLM call, no reason to pay that
    cost for findings you never look at twice.
    """
    cached = tw_mitigations.get_cached(cve_id)
    if cached:
        return jsonify({"guidance": cached["guidance"], "cached": True})

    body = request.json or {}
    description = body.get("description", "")
    matched_asset = body.get("matched_asset", "")
    cvss_score = body.get("cvss_score")
    required_action = body.get("required_action", "")

    guidance = dave_api.generate_mitigation(
        cve_id=cve_id,
        description=description,
        matched_asset=matched_asset,
        cvss_score=cvss_score,
        required_action=required_action,
    )
    tw_mitigations.save(cve_id, guidance, model="dave-api")
    return jsonify({"guidance": guidance, "cached": False})


@app.route("/pulse")
@auth.login_required
def pulse():
    """On-demand only -- deliberately no scheduled timer for this one (see
    pulse/README.md's cost section for why). The page shows whatever the
    last manual pull brought back; nothing refreshes just from loading it.
    """
    pulse_db.init_db()  # no-op if tables already exist; needed before first-ever pull
    with pulse_db.connect() as conn:
        reddit_posts = conn.execute(
            "SELECT subreddit, title, author, score, num_comments, permalink, fetched_at "
            "FROM reddit_posts ORDER BY fetched_at DESC, score DESC LIMIT 40"
        ).fetchall()
        account_tweets = conn.execute(
            "SELECT author_handle, text, like_count, created_at, fetched_at FROM tweets "
            "WHERE source = 'account' ORDER BY fetched_at DESC LIMIT 40"
        ).fetchall()
        keyword_tweets = conn.execute(
            "SELECT author_handle, text, matched_keyword, created_at, fetched_at FROM tweets "
            "WHERE source = 'keyword' ORDER BY fetched_at DESC LIMIT 40"
        ).fetchall()
        last_runs = conn.execute(
            "SELECT collector, status, records_in, detail, run_at FROM ingestion_log "
            "ORDER BY run_at DESC LIMIT 10"
        ).fetchall()

    pulse_brief = get_cached_brief(today_str(), kind="pulse")
    return render_template(
        "pulse.html",
        reddit_posts=reddit_posts,
        account_tweets=account_tweets,
        keyword_tweets=keyword_tweets,
        last_runs=last_runs,
        subreddit_count=len(pulse_config.SUBREDDITS),
        x_account_count=len(pulse_config.X_ACCOUNTS),
        pulse_brief=pulse_brief,
    )


@app.route("/api/pulse/refresh", methods=["POST"])
@auth.login_required
def api_pulse_refresh():
    """The only way pulse's collectors ever run -- no systemd timer. This
    makes a real round of paid X API calls (see pulse/README.md), so it
    only fires on an explicit click, never automatically and never as a
    side effect of a chat question. Runs both collectors synchronously;
    expect this to take anywhere from a few seconds to ~30s depending on
    how many accounts/subreddits respond slowly.
    """
    pulse_db.init_db()
    results = {}
    for label, module in PULSE_COLLECTORS:
        try:
            results[label] = module.run()
        except Exception as exc:  # noqa: BLE001 - report it, don't 500 the whole request
            results[label] = f"error: {exc}"
    return jsonify({"results": results, "ran_at": dt.datetime.now().isoformat()})


if __name__ == "__main__":
    # Dev-only entrypoint. Production run is via gunicorn, see
    # deploy/daveweb.service.
    app.run(host=config.HOST, port=config.PORT, debug=False)
