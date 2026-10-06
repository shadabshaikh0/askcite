"""The Slack bot (Socket Mode: no public URL needed, works inside the company network).

People ask by @mentioning the bot in an allowed channel, or in a direct message.
The answer is posted in the thread. Buttons:
- "Show query": shows the SQL that was run (only to the person who clicks).
- "Run it" / "Reject": approvers allow or refuse queries on sensitive tables.

Slack app setup (once): enable Socket Mode, create an app-level token (connections:write)
-> SLACK_APP_TOKEN; bot token scopes: app_mentions:read, chat:write, im:history, im:read,
im:write, usergroups:read -> SLACK_BOT_TOKEN; subscribe to events app_mention and message.im.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

from askcite.access import AccessRules
from askcite.brain import Answer, Brain, answer_after_approval
from askcite.config import AccessConfig, Group, SlackSource
from askcite.slack_format import answer_blocks, plain_text
from askcite.tools import Asker

log = logging.getLogger(__name__)
_MENTION = re.compile(r"<@[A-Z0-9]+>")


def build_app(brain: Brain, access: AccessRules | None = None, token: str | None = None,
              access_config: AccessConfig | None = None, **app_options):
    from slack_bolt import App

    app = App(token=token or os.environ["SLACK_BOT_TOKEN"], **app_options)
    workspace = brain.workspace
    if access is None:
        access = AccessRules(access_config or workspace.settings.access,
                             usergroup_members=lambda group: app.client.usergroups_users_list(
                                 usergroup=group)["users"])

    def handle_question(event: dict, client, is_dm: bool) -> None:
        user, channel = event.get("user"), event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        question = _MENTION.sub("", event.get("text") or "").strip()
        if not user or not question:
            return
        if not access.channel_allowed(channel, is_dm):
            return  # silently ignore channels the bot is not meant to answer in
        groups = access.groups_for(user)
        if not groups:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text="Sorry, you don't have access to Askcite yet. Please ask an admin.")
            return
        waiting = client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                          text="🔎 Looking in the docs, code and data…")
        answer = brain.ask(question, Asker(user, groups), channel=channel)
        if answer.status == "waiting_approval":
            _save_pending(workspace, answer, user, channel, thread_ts)
        client.chat_update(channel=channel, ts=waiting["ts"], text=plain_text(answer)[:3000],
                           blocks=answer_blocks(answer))

    @app.event("app_mention")
    def on_mention(event, client):
        handle_question(event, client, is_dm=False)

    @app.event("message")
    def on_message(event, client):
        if event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype"):
            handle_question(event, client, is_dm=True)

    @app.action("gw_show_query")
    def on_show_query(ack, body, client):
        ack()
        question_id = int(body["actions"][0]["value"])
        rows = workspace.store.execute("select sql_text, status, row_count from query_audit where question_id = %s "
                                       "order by id", (question_id,)).fetchall() if workspace.store else []
        text = "\n".join(f"*{r['status']}* ({r['row_count'] if r['row_count'] is not None else '-'} rows)\n```"
                         f"{r['sql_text']}```" for r in rows) or "No queries were run for this answer."
        client.chat_postEphemeral(channel=body["channel"]["id"], user=body["user"]["id"],
                                  thread_ts=body["message"].get("thread_ts"), text=text[:3000])

    def decide(body, client, approve: bool) -> None:
        user = body["user"]["id"]
        channel = body["channel"]["id"]
        if "approvers" not in access.groups_for(user):
            client.chat_postEphemeral(channel=channel, user=user, text="Only approvers can do this.")
            return
        audit_id = int(body["actions"][0]["value"])
        store = workspace.store
        pending = store.execute("select * from pending_approval where audit_id = %s and status = 'waiting'",
                                (audit_id,)).fetchone()
        if pending is None:
            client.chat_postEphemeral(channel=channel, user=user, text="This request was already handled.")
            return
        status = "approved" if approve else "rejected"
        store.execute("update pending_approval set status = %s, decided_by = %s, decided_at = now() where id = %s",
                      (status, user, pending["id"]))
        store.execute("update query_audit set status = %s, approved_by = %s, approved_at = now() where id = %s",
                      ("approved" if approve else "rejected", user, audit_id))
        if approve:
            answer = answer_after_approval(workspace, pending, user)
        else:
            answer = Answer(pending["question"], "not_found", f"The query was rejected by <@{user}>.",
                            confidence="high")
        client.chat_update(channel=channel, ts=body["message"]["ts"], text=plain_text(answer)[:3000],
                           blocks=answer_blocks(answer))

    @app.action("gw_approve")
    def on_approve(ack, body, client):
        ack()
        decide(body, client, approve=True)

    @app.action("gw_reject")
    def on_reject(ack, body, client):
        ack()
        decide(body, client, approve=False)

    return app


def _save_pending(workspace, answer: Answer, user: str, channel: str, thread_ts: str) -> None:
    waiting = next((q for q in answer.queries.values() if q["status"] == "waiting_approval"), None)
    if waiting is None or workspace.store is None or not answer.pending_audit_ids:
        return
    workspace.store.execute(
        "insert into pending_approval (audit_id, question, sql_text, answer_template, sources, slack_user, channel, "
        "thread_ts) values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (answer.pending_audit_ids[0], answer.question, waiting["sql"], answer.template,
         json.dumps([s.__dict__ for s in answer.sources]), user, channel, thread_ts),
    )


def run_socket_mode(app, app_token: str | None = None) -> None:
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    SocketModeHandler(app, app_token or os.environ["SLACK_APP_TOKEN"]).start()


def merge_access(base: AccessConfig, slack: SlackSource | None) -> AccessConfig:
    """access.yaml plus the people and channels entered on the Slack connector."""
    merged = base.model_copy(deep=True)
    if slack is None:
        return merged
    merged.allowed_channels = list(dict.fromkeys(merged.allowed_channels + slack.allowed_channels))
    for group_name, users in (("data", slack.data_users), ("approvers", slack.approvers)):
        group = merged.groups.setdefault(group_name, Group())
        group.slack_users = list(dict.fromkeys(group.slack_users + users))
    return merged


class SlackManager:
    """Keeps the Socket Mode connection in step with the saved Slack credentials (no restart needed)."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.handler = None
        self.key = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def ensure(self, slack: SlackSource | None) -> None:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        app_token, bot_token = slack.resolve_tokens() if slack else (None, None)
        key = (app_token, bot_token, tuple(slack.allowed_channels), tuple(slack.data_users),
               tuple(slack.approvers)) if slack else None
        with self._lock:
            if key == self.key:
                return
            self.stop()
            self.key = key
            if not (app_token and bot_token):
                self.error = None
                return
            try:
                app = build_app(self.runtime.brain, token=bot_token,
                                access_config=merge_access(self.runtime.settings.access, slack))
                handler = SocketModeHandler(app, app_token)
                handler.connect()  # non-blocking; the web page keeps running in the main thread
                self.handler, self.error = handler, None
                log.info("Slack bot connected")
            except Exception as error:  # noqa: BLE001 - shown on the connectors page
                self.error = str(error).splitlines()[0][:300]
                log.error("Slack bot could not connect: %s", self.error)

    def stop(self) -> None:
        if self.handler is not None:
            try:
                self.handler.close()
            except Exception:  # noqa: BLE001 - closing a dead connection may fail; that's fine
                pass
            self.handler = None

    def status(self) -> str:
        if self.error:
            return "error"
        if self.handler is None:
            return "not connected"
        client = getattr(self.handler, "client", None)
        connected = getattr(client, "is_connected", lambda: True)()
        return "connected" if connected else "connecting"
