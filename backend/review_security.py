"""Cookie sessions, role checks and audited API access. No default production users."""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from .review_store import audit, connection
except ImportError:
    from review_store import audit, connection


def configure_security(app):
    production = os.getenv("APP_ENV") == "production" or bool(os.getenv("RENDER"))
    try:
        users = json.loads(os.getenv("REVIEW_USERS_JSON", "{}"))
    except ValueError:
        users = {}
    if not isinstance(users, dict) or any(
        not isinstance(value, dict) or value.get("role") not in {"viewer", "reviewer", "admin"}
        or not isinstance(value.get("password_hash"), str)
        for value in users.values()
    ):
        users = {}
    secret = os.getenv("REVIEW_SESSION_SECRET", "")
    app.config.update(
        REVIEW_USERS=users,
        REVIEW_AUTH_REQUIRED=production or bool(users) or os.getenv("REQUIRE_AUTH", "").lower() == "true",
        REVIEW_AUTH_CONFIGURED=bool(users) and len(secret) >= 32,
        REVIEW_DB=os.getenv("REVIEW_DB_PATH", str(Path(__file__).resolve().parent.parent / ".review-data" / "reviews.sqlite3")),
        SECRET_KEY=secret or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    dummy_hash = generate_password_hash(secrets.token_hex(16))

    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/") or request.method == "OPTIONS":
            return None
        g.actor, g.role = "anonymous", "none"
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("Origin")
            trusted = {request.host_url.rstrip("/"), *os.getenv("CORS_ORIGIN", "http://127.0.0.1:5173,http://localhost:5173").split(",")}
            if origin and origin not in trusted:
                return jsonify(message="Request origin is not allowed."), 403
        public = request.path in {"/api/session", "/api/login"}
        if app.config["REVIEW_AUTH_REQUIRED"]:
            if not app.config["REVIEW_AUTH_CONFIGURED"]:
                return jsonify(message="Secure access is not configured. Set review users and a session secret on the server."), 503
            user = app.config["REVIEW_USERS"].get(session.get("username"))
            if user:
                g.actor, g.role = session["username"], user["role"]
            elif not public:
                return jsonify(message="Sign in to access claims."), 401
        else:
            if request.remote_addr not in {"127.0.0.1", "::1"}:
                return jsonify(message="Local demo access only. Configure authentication for remote access."), 403
            g.actor, g.role = "local-demo", "reviewer"
        if not public and request.method not in {"GET", "HEAD"}:
            # Viewers are read-only everywhere.
            if g.role == "viewer":
                return jsonify(message="Viewer access is read-only."), 403
            if app.config["REVIEW_AUTH_REQUIRED"] and not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), session.get("csrf", "invalid")
            ):
                return jsonify(message="Refresh your session before saving."), 403
            if request.path.startswith("/api/reviews/") and g.role not in {"reviewer", "admin"}:
                return jsonify(message="Reviewer access is required."), 403
            if request.path == "/api/rag/rebuild" and g.role != "admin":
                return jsonify(message="Administrator access is required."), 403
        # Review history and audit events are reviewer/admin surfaces.
        if request.path.startswith("/api/reviews/") and request.path.endswith("/history"):
            if g.role not in {"reviewer", "admin"}:
                return jsonify(message="Reviewer access is required."), 403
        if request.path == "/api/review-audit" and g.role != "admin":
            return jsonify(message="Administrator access is required."), 403

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            # Store route patterns rather than member IDs, queries, or request bodies.
            with connection(app.config["REVIEW_DB"]) as db:
                audit(db, getattr(g, "actor", "anonymous"), "api_access",
                      str(request.url_rule or "unmatched"), {"method": request.method, "status": response.status_code})
        return response

    @app.get("/api/session")
    def get_session():
        if g.actor == "anonymous":
            return jsonify(authenticated=False, mode="secure")
        return jsonify(authenticated=True, username=g.actor, role=g.role,
                       mode="secure" if app.config["REVIEW_AUTH_REQUIRED"] else "local_demo",
                       csrf_token=session.get("csrf"))

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 1024:
            return jsonify(message="Invalid credentials."), 400
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        # IP-wide limit prevents bypass by trying different usernames.
        resource = request.remote_addr or "unknown"
        with connection(app.config["REVIEW_DB"]) as db:
            db.execute("BEGIN IMMEDIATE")
            failures = db.execute("SELECT COUNT(*) FROM audit WHERE event='login_failed' AND resource=? AND at>?", (resource, cutoff)).fetchone()[0]
            if failures >= 5:
                return jsonify(message="Too many sign-in attempts. Try again in 15 minutes."), 429
            user = app.config["REVIEW_USERS"].get(username)
            valid = check_password_hash(user["password_hash"] if user else dummy_hash, password)
            if not user or not valid:
                audit(db, "anonymous", "login_failed", resource)
                return jsonify(message="Invalid credentials."), 401
            session.clear()
            session.update(username=username, csrf=secrets.token_hex(32))
            session.permanent = True
            audit(db, username, "login_success", "session")
        return jsonify(authenticated=True, username=username, role=user["role"], mode="secure", csrf_token=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(authenticated=False)

    @app.get("/api/reviews/<review_id>/history")
    def get_review_history(review_id):
        with connection(app.config["REVIEW_DB"]) as db:
            rows = db.execute(
                "SELECT at,actor,event,detail FROM audit WHERE resource=? ORDER BY id ASC",
                (review_id,),
            ).fetchall()
        return jsonify(
            review_id=review_id,
            events=[
                {"at": row["at"], "actor": row["actor"], "event": row["event"],
                 "detail": json.loads(row["detail"] or "{}")}
                for row in rows
            ],
        )

    @app.get("/api/review-audit")
    def get_audit():
        try:
            before = int(request.args.get("before", "9223372036854775807"))
        except ValueError:
            return jsonify(message="Invalid audit cursor."), 400
        with connection(app.config["REVIEW_DB"]) as db:
            rows = db.execute("SELECT * FROM audit WHERE id<? ORDER BY id DESC LIMIT 100", (before,)).fetchall()
        return jsonify(items=[dict(row) for row in rows], next_cursor=rows[-1]["id"] if rows else None)
