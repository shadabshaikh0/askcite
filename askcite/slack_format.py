"""Turn an Answer into a Slack message (Block Kit)."""

from __future__ import annotations

import re

from askcite.brain import Answer

_ICONS = {"doc": "📄", "code": "💻", "sql_example": "💻", "table": "🗄️", "query": "🗄️"}
_MAX_TEXT = 2900


def to_mrkdwn(text: str) -> str:
    """Markdown from the AI -> Slack mrkdwn (bold, links, headings)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    return text


def _source_line(source) -> str:
    icon = _ICONS.get(source.kind, "•")
    title = source.title.replace("|", "/").replace(">", "›")
    label = f"<{source.url}|{title}>" if source.url else title
    return f"{icon} {label}" + (f" — {source.detail}" if source.detail else "")


def answer_blocks(answer: Answer) -> list[dict]:
    if answer.status == "waiting_approval":
        body = ("⏳ *This needs a database query on a sensitive table, which needs approval first.* "
                "An approver can allow it below; the answer will appear here.")
    elif answer.status == "error":
        body = answer.text
    elif answer.status == "not_found":
        body = to_mrkdwn(answer.text) or "I couldn't find this in the docs, code or database."
    else:
        body = to_mrkdwn(answer.text)
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": body[:_MAX_TEXT] or "(empty)"}}]
    if answer.how and answer.status in ("answered", "not_found"):
        how = f"_How I got this:_ {answer.how}"[:_MAX_TEXT]
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": how}]})
    shown = [s for s in answer.sources if s.kind != "table" or not any(x.kind == "query" for x in answer.sources)]
    if shown:
        lines = "\n".join(_source_line(s) for s in shown[:8])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Sources*\n{lines}"[:_MAX_TEXT]}})
    if answer.status in ("answered", "not_found"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                                                         "text": f"Confidence: {answer.confidence.capitalize()}"}]})
    buttons = []
    if answer.queries and answer.question_id is not None:
        buttons.append({"type": "button", "action_id": "gw_show_query", "value": str(answer.question_id),
                        "text": {"type": "plain_text", "text": "Show query"}})
    if answer.status == "waiting_approval" and answer.pending_audit_ids:
        value = str(answer.pending_audit_ids[0])
        buttons += [
            {"type": "button", "action_id": "gw_approve", "style": "primary", "value": value,
             "text": {"type": "plain_text", "text": "Run it (approvers)"}},
            {"type": "button", "action_id": "gw_reject", "style": "danger", "value": value,
             "text": {"type": "plain_text", "text": "Reject"}},
        ]
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return blocks


def plain_text(answer: Answer) -> str:
    """Fallback text for notifications and the CLI."""
    lines = [answer.text]
    if answer.how:
        lines.append(f"How I got this: {answer.how}")
    if answer.sources:
        lines.append("Sources:")
        lines += [f"  {s.id} {s.title}" + (f" ({s.detail})" if s.detail else "") + (f"\n     {s.url}" if s.url else "")
                  for s in answer.sources]
    lines.append(f"Confidence: {answer.confidence}")
    return "\n".join(lines)
