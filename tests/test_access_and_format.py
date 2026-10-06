from askcite.access import AccessRules
from askcite.brain import Answer
from askcite.config import AccessConfig
from askcite.slack_format import answer_blocks, to_mrkdwn
from askcite.tools import Source


def test_groups_from_users_and_usergroups():
    config = AccessConfig.model_validate({
        "allowed_channels": ["C1"],
        "groups": {"everyone": {"all_users": True}, "data": {"slack_usergroups": ["S1"]},
                   "approvers": {"slack_users": ["U9"]}}})
    rules = AccessRules(config, usergroup_members=lambda group: ["U1"] if group == "S1" else [])
    assert rules.groups_for("U1") == {"everyone", "data"}
    assert rules.groups_for("U9") == {"everyone", "approvers"}
    assert rules.channel_allowed("C1", False) and not rules.channel_allowed("C2", False)
    assert rules.channel_allowed("D1", True)


def test_answer_blocks_show_sources_and_buttons():
    answer = Answer("q", "answered", "**1,234** orders", how="Counted.", confidence="high", question_id=5,
                    sources=[Source("D1", "doc", "Settlement TRD › States", "https://notion.so/x#b"),
                             Source("Q1", "query", "Database query", None, "run 10:32 · 1 row(s)")],
                    queries={"Q1": {"sql": "select 1", "tables": [], "status": "ok", "rows": 1}})
    blocks = answer_blocks(answer)
    assert blocks[0]["text"]["text"] == "*1,234* orders"
    sources = next(b for b in blocks if b["type"] == "section" and "Sources" in b["text"]["text"])["text"]["text"]
    assert "<https://notion.so/x#b|Settlement TRD › States>" in sources and "🗄️ Database query" in sources
    assert blocks[-1]["elements"][0]["action_id"] == "gw_show_query"


def test_markdown_links_become_slack_links():
    assert to_mrkdwn("see [TRD](https://n.so/x)") == "see <https://n.so/x|TRD>"
