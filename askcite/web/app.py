"""The Connectors web page (and a small "Try a question" page).

Server-rendered with Jinja2; no build step, no external scripts. Everything except /healthz
and /static needs the admin password, and every POST needs the CSRF token.
"""

from __future__ import annotations

import hmac
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from askcite.connectors import REGISTRY
from askcite.connectors.store import delete_connector, get_connector, latest_syncs, list_connectors, save_connector
from askcite.web.auth import PasswordChecker, csrf_token

_HERE = Path(__file__).parent
_ICONS = {"slack": "💬", "notion": "📄", "git": "💻", "postgres": "🗄️", "folder": "📁"}
_ORDER = ["slack", "git", "notion", "folder", "postgres"]


def _ago(moment: datetime | None) -> str:
    if moment is None:
        return "never"
    seconds = int((datetime.now(UTC) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _answer_html(text: str) -> Markup:
    """Very small Markdown subset for answers: **bold**, `code`, ``` blocks, line breaks."""
    html = str(escape(text))
    html = re.sub(r"```\n?(.*?)```", lambda m: f"<pre>{m.group(1)}</pre>", html, flags=re.S)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    return Markup(html.replace("\n", "<br>"))


def _summary(key: str, stats: dict) -> str:
    if key.startswith("git:"):
        commit = f" · commit {str(stats.get('commit', ''))[:8]}" if stats.get("commit") else ""
        if "total_functions" in stats:
            return (f"{stats['total_files']} files · {stats['total_functions']} functions · "
                    f"{stats['total_sql_examples']} SQL queries{commit}")
        return commit.removeprefix(" · ")
    if key.startswith("folder:"):
        return f"{stats.get('files', 0)} documents · {stats.get('total_sections', stats.get('sections', 0))} sections"
    if key == "notion":
        return f"{stats.get('pages_seen', 0)} pages shared · {stats.get('sections', 0)} sections updated"
    if key.startswith("schema:"):
        return f"{stats.get('tables', 0)} tables ({stats.get('origin', '')})"
    return ""


def create_app(runtime, public_demo: bool = False) -> FastAPI:
    """public_demo: visitors may use /ask and see a read-only /connectors without a password, with limits."""
    from askcite.web.cache import cached_answer, store_answer
    from askcite.web.limits import QuestionLimiter

    app = FastAPI(title="Askcite", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.filters["ago"] = _ago
    templates.env.filters["answer_html"] = _answer_html
    security = HTTPBasic(realm="Askcite", auto_error=False)
    checker = PasswordChecker(runtime.store)
    token = csrf_token(runtime.box.derive(b"csrf"))
    demo = runtime.settings.sources.demo
    limiter = QuestionLimiter(demo.per_visitor_limit, demo.per_visitor_window_minutes, demo.daily_limit)

    def _valid(credentials: HTTPBasicCredentials | None) -> bool:
        return credentials is not None and checker.check(credentials.username, credentials.password)

    def admin(credentials: HTTPBasicCredentials | None = Depends(security)) -> bool:
        if not _valid(credentials):
            raise HTTPException(401, "Wrong user name or password",
                                headers={"WWW-Authenticate": 'Basic realm="Askcite"'})
        return True

    def viewer(credentials: HTTPBasicCredentials | None = Depends(security)) -> bool:
        """True for the admin. In the public demo, visitors get in too (as False)."""
        if public_demo:
            return _valid(credentials)
        return admin(credentials)

    async def check_post(request: Request) -> dict:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}":
            raise HTTPException(403, "Cross-site request refused")
        form = dict(await request.form())
        sent = form.get("csrf") or request.headers.get("x-csrf-token") or ""
        if not hmac.compare_digest(str(sent), token):
            raise HTTPException(403, "Missing or invalid CSRF token — reload the page")
        return form

    def page(request: Request, template: str, **context) -> HTMLResponse:
        base = {"csrf": token, "public_demo": public_demo, "demo": demo, "is_admin": True}
        return templates.TemplateResponse(request, template, {**base, **context})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def home(is_admin: bool = Depends(viewer)):
        return RedirectResponse("/ask" if public_demo and not is_admin else "/connectors", status_code=303)

    @app.get("/login", dependencies=[Depends(admin)])
    def login():
        """Asking for the password here makes the browser send it on every page of the site."""
        return RedirectResponse("/connectors", status_code=303)

    @app.get("/connectors", response_class=HTMLResponse)
    def connectors_page(request: Request, saved: str | None = None, removed: str | None = None,
                        syncing: int = 0, is_admin: bool = Depends(viewer)):
        syncs = latest_syncs(runtime.store)
        records = list_connectors(runtime.store)
        yaml_sources = runtime.settings.sources
        groups, running = [], False
        for kind in _ORDER:
            connector = REGISTRY[kind]
            items = []
            for record in [r for r in records if r.kind == kind]:
                items.append({"id": record.id, "name": record.name, "origin": "connector",
                              "enabled": record.enabled, "keys": connector.source_keys(record.name)})
            if kind == "git":
                items += [{"name": s.name, "origin": "sources.yaml", "keys": [f"git:{s.name}"]}
                          for s in yaml_sources.code if s.name not in {i["name"] for i in items}]
            elif kind == "folder":
                items += [{"name": f.name, "origin": "sources.yaml", "keys": [f"folder:{f.name}"]}
                          for f in yaml_sources.docs_folders if f.name not in {i["name"] for i in items}]
            elif kind == "notion" and yaml_sources.notion and not items:
                items.append({"name": "notion", "origin": "sources.yaml", "keys": ["notion"]})
            elif kind == "postgres" and yaml_sources.database and not items:
                items.append({"name": yaml_sources.database.name, "origin": "sources.yaml",
                              "keys": [f"schema:{yaml_sources.database.name}"]})
            elif kind == "slack" and not items and runtime.sources.slack is not None:
                items.append({"name": "slack", "origin": ".env", "keys": []})
            for item in items:
                runs = [dict(syncs[k], key=k) for k in item["keys"] if k in syncs]
                item["runs"] = [{**r, "summary": _summary(r["key"], (r.get("last_ok") or {}).get("stats") or {})}
                                for r in runs]
                if kind == "slack":
                    state = runtime.slack.status() if runtime.slack else "not running"
                    item["state"] = {"connected": "ok", "connecting": "running"}.get(state, "failed"
                                                                                    if state == "error" else "idle")
                    item["state_text"] = (runtime.slack.error if runtime.slack and runtime.slack.error
                                          else f"Bot {state}")
                elif not runs:
                    item["state"], item["state_text"] = "idle", "Waiting for the first sync"
                else:
                    statuses = {r["status"] for r in runs}
                    item["state"] = "running" if "running" in statuses else (
                        "failed" if "failed" in statuses else "ok")
                    item["state_text"] = {"running": "Syncing…", "failed": "Last sync failed",
                                          "ok": "Connected"}[item["state"]]
                running = running or item["state"] == "running"
            can_add = connector.multiple or not any(i.get("origin") == "connector" for i in items)
            groups.append({"kind": kind, "icon": _ICONS[kind], "connector": connector, "entries": items,
                           "can_add": can_add})
        return page(request, "connectors.html", groups=groups, saved=saved, removed=removed,
                    running=running or bool(syncing), is_admin=is_admin)

    def _form_context(kind: str, record=None, values=None, error=None, result=None):
        connector = REGISTRY.get(kind)
        if connector is None:
            raise HTTPException(404, "Unknown connector")
        config = values if values is not None else (record.config if record else {})
        saved_secrets = runtime.box.decrypt(record.secrets_encrypted) if record else {}
        fields = []
        for field in connector.fields:
            value = config.get(field.name, field.default) if not field.secret else ""
            if isinstance(value, list):
                value = ", ".join(value)
            fields.append({"field": field, "value": "" if value is None else value,
                           "saved": bool(saved_secrets.get(field.name))})
        return {"kind": kind, "icon": _ICONS[kind], "connector": connector, "record": record, "fields": fields,
                "name": (record.name if record else (values or {}).get("_name", "")), "error": error,
                "result": result}

    @app.get("/connectors/new/{kind}", response_class=HTMLResponse, dependencies=[Depends(admin)])
    def new_connector(request: Request, kind: str):
        return page(request, "form.html", **_form_context(kind))

    @app.get("/connectors/{connector_id}/edit", response_class=HTMLResponse, dependencies=[Depends(admin)])
    def edit_connector(request: Request, connector_id: int):
        record = get_connector(runtime.store, connector_id)
        if record is None:
            raise HTTPException(404, "Connector not found")
        return page(request, "form.html", **_form_context(record.kind, record))

    def _parse(form: dict):
        kind = str(form.get("kind", ""))
        connector = REGISTRY.get(kind)
        if connector is None:
            raise HTTPException(400, "Unknown connector")
        record = get_connector(runtime.store, int(form["id"])) if form.get("id") else None
        previous = runtime.box.decrypt(record.secrets_encrypted) if record else {}
        config, secrets = connector.parse(form, previous)
        return connector, record, config, secrets

    @app.post("/connectors/test", dependencies=[Depends(admin)])
    def test_connector(form: dict = Depends(check_post)):
        connector, _, config, secrets = _parse(form)
        missing = connector.missing(config, secrets)
        if missing:
            return JSONResponse({"ok": False, "message": "Please fill in: " + ", ".join(missing), "details": [],
                                 "warnings": [], "blocked": False})
        return JSONResponse(connector.test(config, secrets, runtime.settings).as_dict())

    @app.post("/connectors/save", response_class=HTMLResponse, dependencies=[Depends(admin)])
    def save(request: Request, form: dict = Depends(check_post)):
        connector, record, config, secrets = _parse(form)
        name = (str(form.get("_name") or "").strip() or (record.name if record else "")
                or connector.default_name(config))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", name):
            return page(request, "form.html", **_form_context(connector.kind, record, {**config, "_name": name},
                                                             error="Name: letters, digits, . _ - only"))
        clash = get_connector(runtime.store, name)
        if clash is not None and (record is None or clash.id != record.id):
            return page(request, "form.html", **_form_context(connector.kind, record, {**config, "_name": name},
                                                             error=f"The name “{name}” is already used"))
        missing = connector.missing(config, secrets)
        if missing:
            return page(request, "form.html", **_form_context(connector.kind, record, {**config, "_name": name},
                                                             error="Please fill in: " + ", ".join(missing)))
        result = connector.test(config, secrets, runtime.settings)
        if result.blocked or (not result.ok and not form.get("save_anyway")):
            return page(request, "form.html", **_form_context(connector.kind, record, {**config, "_name": name},
                                                             error=result.message, result=result.as_dict()))
        save_connector(runtime.store, runtime.box, connector.kind, name, config, secrets,
                       connector_id=record.id if record else None)
        runtime.reload_sources()
        runtime.request_sync()
        return RedirectResponse(f"/connectors?saved={name}&syncing=1", status_code=303)

    @app.post("/connectors/{connector_id}/test", dependencies=[Depends(admin)])
    def test_saved(connector_id: int, form: dict = Depends(check_post)):
        record = get_connector(runtime.store, connector_id)
        if record is None:
            raise HTTPException(404, "Connector not found")
        connector = REGISTRY[record.kind]
        result = connector.test(record.config, runtime.box.decrypt(record.secrets_encrypted), runtime.settings)
        return JSONResponse(result.as_dict())

    @app.post("/connectors/{connector_id}/delete", dependencies=[Depends(admin)])
    def remove(connector_id: int, form: dict = Depends(check_post)):
        from askcite.indexing import forget_source

        record = get_connector(runtime.store, connector_id)
        if record is None:
            raise HTTPException(404, "Connector not found")
        forget_source(runtime.store, record.kind, record.name)
        delete_connector(runtime.store, record.id)
        runtime.reload_sources()
        return RedirectResponse(f"/connectors?removed={record.name}", status_code=303)

    @app.post("/sync", dependencies=[Depends(admin)])
    def sync_now(form: dict = Depends(check_post)):
        runtime.request_sync()
        return RedirectResponse("/connectors?syncing=1", status_code=303)

    @app.get("/ask", response_class=HTMLResponse)
    def ask_page(request: Request, is_admin: bool = Depends(viewer)):
        return page(request, "ask.html", question="", answer=None, is_admin=is_admin)

    @app.post("/ask", response_class=HTMLResponse)
    def ask(request: Request, form: dict = Depends(check_post), is_admin: bool = Depends(viewer)):
        question = str(form.get("question") or "").strip()
        context = {"question": question, "answer": None, "notice": None, "cached": False, "is_admin": is_admin}
        if not question:
            return page(request, "ask.html", **context)
        if not public_demo:
            from askcite.tools import Asker

            context["answer"] = runtime.brain.ask(question, Asker("web-admin", {"everyone", "data"}))
            return page(request, "ask.html", **context)
        if len(question) > demo.max_question_chars:
            context["notice"] = f"Please keep questions under {demo.max_question_chars} characters."
            return page(request, "ask.html", **context)
        answer = cached_answer(runtime.store, question, demo.cache_hours)
        if answer is not None:
            context.update(answer=answer, cached=True)
            return page(request, "ask.html", **context)
        visitor = request.client.host if request.client else "unknown"
        refusal = None if is_admin else limiter.refusal(visitor)
        if refusal:
            context["notice"] = refusal
            return page(request, "ask.html", **context)
        if not limiter.try_start():
            context["notice"] = ("Askcite is answering someone else's question right now (the demo runs on a free AI "
                                 "plan, one question at a time). Please try again in a minute.")
            return page(request, "ask.html", **context)
        try:
            limiter.record(visitor)
            context["answer"] = answer_public_question(runtime, question)
        finally:
            limiter.finish()
        store_answer(runtime.store, question, context["answer"])
        return page(request, "ask.html", **context)

    app.state.limiter = limiter

    return app


def answer_public_question(runtime, question: str):
    """Visitors of the public demo may ask about the (fake) data too."""
    from askcite.tools import Asker

    return runtime.brain.ask(question, Asker("demo-visitor", {"everyone", "data"}))


def warm_cache(runtime, questions: list[str], pause_seconds: int = 30, sleep=None) -> int:
    """Answer the suggested questions once in the background, so visitors get them instantly."""
    import time

    from askcite.web.cache import cached_answer, store_answer

    sleep = sleep or time.sleep
    warmed = 0
    for question in questions:
        if cached_answer(runtime.store, question, runtime.settings.sources.demo.cache_hours) is not None:
            continue
        answer = answer_public_question(runtime, question)
        store_answer(runtime.store, question, answer)
        warmed += answer.status == "answered"
        sleep(pause_seconds)  # stay well under free-tier request limits
    return warmed
