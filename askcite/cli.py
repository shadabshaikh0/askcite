"""The `askcite` command."""

from __future__ import annotations

import json
import logging
import threading
import warnings
from pathlib import Path

import typer

app = typer.Typer(help="Askcite: answers from your code, Notion docs and database — in Slack.",
                  no_args_is_help=True, add_completion=False)
log = logging.getLogger("askcite")


def _settings():
    from askcite.config import get_settings

    return get_settings()


def _runtime(settings=None):
    from askcite.runtime import Runtime
    from askcite.storage.db import init_db

    settings = settings or _settings()
    init_db(settings.store_url)  # cheap; makes sure the connector tables exist
    return Runtime(settings)


def make_workspace(settings=None):
    """Workspace with the effective sources (sources.yaml + saved connectors)."""
    return _runtime(settings).workspace


def make_brain(workspace):
    from askcite.brain import Brain
    from askcite.llm import models_from_settings

    cloud, local = models_from_settings(workspace.settings.sources.ai)
    return Brain(workspace, cloud, local)


@app.command("init-db")
def init_db_command():
    """Create Askcite's own tables (safe to run again)."""
    from askcite.storage.db import init_db

    applied = init_db(_settings().store_url)
    typer.echo(f"Applied: {', '.join(applied)}" if applied else "Already up to date.")


@app.command()
def index(what: str = typer.Argument("all", help="code | notion | docs | schema | all"),
          full: bool = typer.Option(False, help="re-read everything, not just changes"),
          schema_from_file: bool = typer.Option(False, help="read the schema file even if a database URL is set")):
    """Read code, Notion, docs folders and the database schema into Askcite's search index."""
    from askcite.indexing import index_code, index_docs_folders, index_notion, index_schema
    from askcite.storage.db import connect

    runtime = _runtime()
    settings, sources = runtime.settings, runtime.sources
    with connect(settings.store_url) as store:
        if what in ("schema", "all") and sources.database:
            typer.echo(f"schema: {index_schema(settings, store, prefer_file=schema_from_file, sources=sources)}")
        if what in ("code", "all") and sources.code:
            for report in index_code(settings, store, full=full, sources=sources):
                typer.echo(f"code: {report}")
        if what in ("notion", "all") and sources.notion:
            typer.echo(f"notion: {index_notion(settings, store, full=full, sources=sources)}")
        if what in ("docs", "all") and sources.docs_folders:
            for report in index_docs_folders(settings, store, sources=sources, full=full):
                typer.echo(f"docs: {report}")


@app.command("fake-db")
def fake_db(rows: int = typer.Option(200, help="fake rows per table"),
            database: str = typer.Option("askcite_fake", help="name of the fake database to (re)create")):
    """Build a FAKE test database from the schema (made-up rows only; never real data)."""
    from askcite.data.fakedb import build_fake_database
    from askcite.indexing import load_catalog
    from askcite.storage.db import connect

    runtime = _runtime()
    settings = runtime.settings
    if runtime.sources.database is None:
        raise typer.BadParameter("No database is connected")
    with connect(settings.store_url) as store:
        catalog = load_catalog(store, runtime.sources.database.name)
    if catalog is None:
        raise typer.BadParameter("No schema yet. Run: askcite index schema")
    report = build_fake_database(catalog, settings.store_url, database=database, rows_per_table=rows)
    typer.echo(f"Fake database '{database}': {report['tables']} tables, {report['rows']} fake rows.")
    if report["skipped"]:
        typer.echo(f"Skipped {len(report['skipped'])} tables: {report['skipped'][:5]}")
    typer.echo(f"For local testing put this in .env:\nREADONLY_DB_URL={report['readonly_url']}")


@app.command("check-sql")
def check_sql(sql: str):
    """Show whether a query would pass Askcite's safety checks (does not run it)."""
    workspace = make_workspace()
    if workspace.guard is None:
        raise typer.BadParameter("No schema yet. Run: askcite index schema")
    result = workspace.guard.check(sql)
    typer.echo(result.explain() + (" (needs approval)" if result.needs_approval else ""))
    typer.echo(f"Tables: {', '.join(result.tables) or '-'}")


@app.command()
def ask(question: str, data: bool = typer.Option(True, help="allow database questions"),
        show_sql: bool = typer.Option(False, help="print the SQL that was run")):
    """Ask a question from the terminal (same brain as the Slack bot)."""
    from askcite.slack_format import plain_text
    from askcite.tools import Asker

    workspace = make_workspace()
    answer = make_brain(workspace).ask(question, Asker("cli", {"everyone", "data"} if data else {"everyone"}))
    typer.echo(plain_text(answer))
    if answer.status == "error":
        typer.echo(f"[error] {answer.how}", err=True)
    if show_sql:
        for query_id, query in answer.queries.items():
            typer.echo(f"\n{query_id} [{query['status']}, rows={query['rows']}]\n{query['sql']}")


@app.command("glossary-draft")
def glossary_draft(out: Path = typer.Option(Path("glossary.draft.yaml")), top: int = 40):
    """Write a glossary skeleton for the tables your code uses most. Fill in the meanings by hand."""
    import yaml

    from askcite.indexing import load_catalog
    from askcite.storage.db import connect

    runtime = _runtime()
    settings = runtime.settings
    with connect(settings.store_url) as store:
        catalog = load_catalog(store, runtime.sources.database.name) if runtime.sources.database else None
        usage = store.execute("select t as table_name, count(*) as uses from sql_example, unnest(tables) t "
                              "group by t order by uses desc limit %s", (top,)).fetchall()
    tables = {}
    for row in usage:
        table = catalog.table(row["table_name"]) if catalog else None
        if table is None:
            continue
        existing = settings.glossary.tables.get(table.name)
        tables[table.name] = {
            "description": (existing.description if existing else "") or f"TODO (used in {row['uses']} queries)",
            "columns": {c.name: (existing.columns.get(c.name, "") if existing else "") for c in table.columns
                        if c.allowed_values or c.name.endswith(("status", "type", "_at", "timestamp", "amount"))},
        }
    out.write_text(yaml.safe_dump({"tables": tables, "terms": dict(settings.glossary.terms)}, sort_keys=False,
                                  allow_unicode=True))
    typer.echo(f"Wrote {out} with {len(tables)} tables. Fill in the TODOs and save it as glossary.yaml.")


@app.command("eval")
def evaluate(questions: Path, only: str = typer.Option("", help="comma-separated question ids"),
             out: Path = typer.Option(Path("eval-report.json")),
             markdown: Path = typer.Option(None, help="also write a Markdown results table here")):
    """Run the test questions and score them (data questions run on the connected database)."""
    from askcite.evals import run_eval, to_markdown

    workspace = make_workspace()
    report = run_eval(make_brain(workspace), questions, only=[q for q in only.split(",") if q])
    out.write_text(json.dumps(report, indent=2, default=str))
    if markdown:
        markdown.write_text(to_markdown(report, workspace.settings.sources.ai.cloud_model))
    summary = report["summary"]
    typer.echo(f"{summary['questions']} questions · source match {summary['source_match']:.0%} · "
               f"tables match {summary['tables_match']:.0%} · answered {summary['answered']:.0%} · "
               f"blocked unsafe SQL {summary['blocked']} · report: {out}")


# ---- connectors ----------------------------------------------------------------------------------

def _prompt_values(connector, previous_config: dict, previous_secrets: dict, preset: dict[str, str],
                   interactive: bool) -> dict:
    values: dict[str, object] = {}
    for field in connector.fields:
        if field.show_if and str(values.get(field.show_if[0])) != field.show_if[1]:
            continue
        if field.name in preset:
            values[field.name] = preset[field.name]
            continue
        current = previous_config.get(field.name, field.default)
        if not interactive:
            values[field.name] = current
            continue
        hint = f" ({field.help})" if field.help else ""
        if field.kind == "checkbox":
            values[field.name] = typer.confirm(field.label, default=bool(current))
        elif field.kind == "select":
            choices = [value for value, _ in field.options]
            labels = ", ".join(f"{value} = {label}" for value, label in field.options)
            values[field.name] = typer.prompt(f"{field.label} [{labels}]", default=current or choices[0],
                                              type=click_choice(choices))
        elif field.secret and field.kind == "textarea":
            saved = " (Enter keeps the saved key)" if previous_secrets.get(field.name) else ""
            path = typer.prompt(f"{field.label}: path to the file{saved}", default="", show_default=False)
            values[field.name] = Path(path).expanduser().read_text() if path else ""
        elif field.secret:
            saved = " (Enter keeps the saved value)" if previous_secrets.get(field.name) else ""
            values[field.name] = typer.prompt(f"{field.label}{saved}", default="", show_default=False,
                                              hide_input=True)
        else:
            default = ", ".join(current) if isinstance(current, list) else (current if current is not None else "")
            values[field.name] = typer.prompt(f"{field.label}{hint}", default=str(default),
                                              show_default=bool(default))
    return values


def click_choice(choices):
    import click

    return click.Choice(choices)


def _print_test(result) -> None:
    typer.echo(("✓ " if result.ok else "✗ ") + result.message)
    for line in result.details:
        typer.echo(f"   {line}")
    for line in result.warnings:
        typer.echo(f"   ⚠ {line}")


@app.command()
def connect(kind: str = typer.Argument(..., help="slack | notion | git | postgres | folder"),
            name: str = typer.Option(None, help="name for this connector (e.g. the repo name)"),
            set_: list[str] = typer.Option([], "--set", help="field=value, can be repeated (skips that prompt)"),
            yes: bool = typer.Option(False, "--yes", help="no prompts: use --set values and defaults"),
            no_sync: bool = typer.Option(False, help="save without running the first sync")):
    """Connect a source (or edit an existing one): asks a few questions, tests it, saves it encrypted."""
    from askcite.connectors import REGISTRY
    from askcite.connectors.store import get_connector, list_connectors, save_connector

    connector = REGISTRY.get(kind)
    if connector is None:
        raise typer.BadParameter(f"unknown kind {kind}; choose from {', '.join(REGISTRY)}")
    runtime = _runtime()
    existing = get_connector(runtime.store, name) if name else None
    if existing is None and not connector.multiple:
        existing = next((c for c in list_connectors(runtime.store) if c.kind == kind), None)
    if existing is not None and existing.kind != kind:
        raise typer.BadParameter(f"{name} is already used by a {existing.kind} connector")
    previous_config = existing.config if existing else {}
    previous_secrets = runtime.box.decrypt(existing.secrets_encrypted) if existing else {}
    preset = dict(item.split("=", 1) for item in set_)
    typer.echo(f"{'Editing' if existing else 'Connecting'} {connector.title}: {connector.description}")
    values = _prompt_values(connector, previous_config, previous_secrets, preset, interactive=not yes)
    config, secrets = connector.parse(values, previous_secrets)
    missing = connector.missing(config, secrets)
    if missing:
        raise typer.BadParameter(f"missing: {', '.join(missing)}")
    final_name = (existing.name if existing else None) or name or connector.default_name(config)
    if not yes and not existing and not name:
        final_name = typer.prompt("Name", default=final_name)
    typer.echo("Testing…")
    result = connector.test(config, secrets, runtime.settings)
    _print_test(result)
    if result.blocked:
        typer.echo("Not saved.")
        raise typer.Exit(1)
    if not result.ok and (yes or not typer.confirm("The test failed. Save anyway?", default=False)):
        raise typer.Exit(1)
    save_connector(runtime.store, runtime.box, kind, final_name, config, secrets,
                   connector_id=existing.id if existing else None)
    typer.echo(f"Saved “{final_name}” (secrets encrypted).")
    runtime.reload_sources()
    if not no_sync:
        for key in connector.source_keys(final_name):
            typer.echo(f"Syncing {key}…")
            typer.echo(f"   {runtime.sync(only=key).get(key, 'nothing to do')}")


@app.command("connectors")
def connectors_status():
    """Show every source and its last sync."""
    from askcite.connectors import REGISTRY
    from askcite.connectors.store import latest_syncs, list_connectors

    runtime = _runtime()
    syncs = latest_syncs(runtime.store)
    rows = [(c.name, c.kind, "connector" if c.enabled else "disabled", REGISTRY[c.kind].source_keys(c.name))
            for c in list_connectors(runtime.store) if c.kind in REGISTRY]
    yaml_sources = runtime.settings.sources
    rows += [(s.name, "git", "sources.yaml", [f"git:{s.name}"]) for s in yaml_sources.code]
    rows += [(f.name, "folder", "sources.yaml", [f"folder:{f.name}"]) for f in yaml_sources.docs_folders]
    if yaml_sources.notion:
        rows.append(("notion", "notion", "sources.yaml", ["notion"]))
    if yaml_sources.database:
        rows.append((yaml_sources.database.name, "postgres", "sources.yaml", [f"schema:{yaml_sources.database.name}"]))
    if not rows:
        typer.echo("Nothing connected yet. Try: askcite connect git")
        return
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(runtime.settings.sources.timezone)
    for name, kind, origin, keys in rows:
        states = []
        for key in keys:
            run = syncs.get(key)
            states.append(f"{run['status']} {run['started_at'].astimezone(zone):%d %b %H:%M}" if run
                          else "not synced yet")
            if run and run.get("error"):
                states.append(f"error: {run['error'][:80]}")
        typer.echo(f"{name:<24} {kind:<9} {origin:<13} {' · '.join(states) or '-'}")


@app.command()
def sync(name: str = typer.Argument(None, help="a connector/source name, or a key like git:shop; empty = all")):
    """Sync sources now (instead of waiting for the background refresh)."""
    from askcite.connectors import REGISTRY
    from askcite.connectors.store import get_connector

    runtime = _runtime()
    record = get_connector(runtime.store, name) if name else None
    if not name:
        keys = [None]  # everything
    elif record is not None:
        keys = REGISTRY[record.kind].source_keys(record.name)
    elif ":" in name or name == "notion":
        keys = [name]
    else:  # a source from sources.yaml: try each kind of key
        keys = [f"git:{name}", f"folder:{name}", f"schema:{name}"]
    for key in keys:
        for source, state in runtime.sync(only=key).items():
            typer.echo(f"{source:<32} {state}")


@app.command()
def disconnect(name: str, yes: bool = typer.Option(False, "--yes")):
    """Remove a connector and everything indexed from it."""
    from askcite.connectors.store import delete_connector, get_connector
    from askcite.indexing import forget_source

    runtime = _runtime()
    record = get_connector(runtime.store, name)
    if record is None:
        raise typer.BadParameter(f"no connector named {name}")
    if not yes and not typer.confirm(f"Remove {record.kind} connector “{name}” and its indexed data?"):
        raise typer.Exit(1)
    forget_source(runtime.store, record.kind, record.name)
    delete_connector(runtime.store, record.id)
    typer.echo(f"Removed {name}.")


@app.command("admin-password")
def admin_password():
    """Set the password for the web page (user name: admin)."""
    from askcite.web.auth import set_admin_password

    runtime = _runtime()
    password = typer.prompt("New admin password", hide_input=True, confirmation_prompt=True)
    if len(password) < 10:
        raise typer.BadParameter("use at least 10 characters")
    set_admin_password(runtime.store, password)
    typer.echo("Saved. Restart `askcite run` if it is running.")


# ---- demo --------------------------------------------------------------------------------------

demo_app = typer.Typer(help="Try Askcite with the fake demo shop.", no_args_is_help=True)
app.add_typer(demo_app, name="demo")


@demo_app.command("setup")
def demo_setup(example: Path = typer.Option(Path("examples/demo-shop"), help="the demo folder"),
               rows: int = typer.Option(300, help="fake rows per table"),
               docs_web_url: str = typer.Option(None, help="base URL for doc links, e.g. your GitHub blob URL"),
               in_place: bool = typer.Option(False, help="use ASKCITE_STORE_URL/ASKCITE_DATA_DIR as they are "
                                                         "(containers) instead of a separate demo database"),
               skip_if_ready: bool = typer.Option(False, help="do nothing if the demo is already set up")):
    """Set up the demo shop: a git repo, a FAKE database, connectors and the first sync."""
    from askcite.demo import demo_is_ready, setup_demo

    if not (example / "app").is_dir():
        raise typer.BadParameter(f"{example} does not look like the demo folder (run from the repo root)")
    settings = _settings()
    if skip_if_ready and in_place and demo_is_ready(settings.store_url):
        typer.echo("Demo already set up — nothing to do.")
        return
    report = setup_demo(example, settings.store_url, rows=rows, docs_web_url=docs_web_url, echo=typer.echo,
                        in_place=in_place, data_dir=settings.data_dir if in_place else None)
    for source, state in report["sync"].items():
        typer.echo(f"   {source:<28} {state}")
    if in_place:
        return
    config_dir = report["config_dir"]
    typer.echo(f"""
Done. Fake shop database: {report['tables']} tables, {report['rows']} made-up rows.

Next:
  ASKCITE_CONFIG_DIR={config_dir} askcite run
  → open http://127.0.0.1:8080/connectors (user: admin; the password is printed on first start)

Or ask from the terminal (needs Ollama with `ollama pull qwen2.5:7b`, or another model in sources.yaml):
  ASKCITE_CONFIG_DIR={config_dir} askcite ask "What happens when a payment fails?"
""")


# ---- run -----------------------------------------------------------------------------------------

@app.command()
def run(host: str = typer.Option("127.0.0.1", help="web page address (keep 127.0.0.1 unless behind a proxy)"),
        port: int = typer.Option(8080),
        refresh_minutes: int = typer.Option(10, help="how often to pick up code/Notion/docs/schema changes"),
        public_demo: bool = typer.Option(False, envvar="ASKCITE_PUBLIC_DEMO",
                                         help="anyone may use /ask and see a read-only /connectors, with limits"),
        trust_proxy: bool = typer.Option(False, envvar="ASKCITE_TRUST_PROXY",
                                         help="behind a reverse proxy (Caddy, nginx): use X-Forwarded-For/Proto")):
    """Start everything: the Connectors web page, the Slack bot and background syncing."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)  # one line per model call is noise
    warnings.filterwarnings("ignore", message=".*ReadOnly.* qualifier", category=UserWarning)  # litellm import
    import uvicorn

    from askcite.slack_bot import SlackManager
    from askcite.web.app import create_app
    from askcite.web.auth import ensure_admin_password

    runtime = _runtime()
    generated = ensure_admin_password(runtime.store)
    runtime.slack = SlackManager(runtime)
    runtime.slack.ensure(runtime.sources.slack)
    threading.Thread(target=runtime.refresh_loop, args=(refresh_minutes,), daemon=True).start()
    if public_demo and runtime.settings.sources.demo.suggested_questions:
        from askcite.web.app import warm_cache

        threading.Thread(target=warm_cache, args=(runtime, runtime.settings.sources.demo.suggested_questions),
                         daemon=True).start()
    url = f"http://{host}:{port}/{'ask' if public_demo else 'connectors'}"
    log.info("Askcite is running%s — open %s", " (public demo mode)" if public_demo else "", url)
    if generated:
        log.warning("First start: the web page password is  %s  (user: admin; change it with "
                    "`askcite admin-password`)", generated)
    uvicorn.run(create_app(runtime, public_demo=public_demo), host=host, port=port, log_level="warning",
                proxy_headers=trust_proxy, forwarded_allow_ips="*" if trust_proxy else None)


if __name__ == "__main__":
    app()
