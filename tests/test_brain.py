import json

from askcite.brain import Brain
from askcite.data.runner import QueryResult
from askcite.tools import Asker, Workspace

SECRET_NUMBER = 987654321


class FakeRunner:
    def __init__(self):
        self.sql = []

    def run(self, sql):
        self.sql.append(sql)
        return QueryResult(columns=["count"], rows=[(SECRET_NUMBER,)], truncated=False, duration_ms=3)


class ScriptedModel:
    """Plays back a fixed list of tool calls and records everything it was sent."""
    name = "scripted"

    def __init__(self, steps, is_local=False):
        self.steps, self.is_local, self.sent = list(steps), is_local, []

    def chat(self, messages, tools=None, tool_choice=None):
        self.sent.append(json.dumps(messages, default=str))
        name, args = self.steps.pop(0)
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call{len(self.sent)}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]}


def workspace(settings, catalog, runner=None):
    settings.sources.database.approval_tables = ["payments"]
    return Workspace(settings, store=None, catalog=catalog, runner=runner or FakeRunner())


def test_cloud_model_never_sees_data_values(settings, catalog):
    model = ScriptedModel([
        ("describe_table", {"table": "orders"}),
        ("run_query", {"sql": "select count(*) from orders where status = 'SETTLED'", "purpose": "settled"}),
        ("final_answer", {"answer": "{{Q1}} orders were settled.", "how": "Counted SETTLED orders.",
                          "sources": ["T1", "Q1"], "confidence": "high", "found": True}),
    ])
    answer = Brain(workspace(settings, catalog), model).ask("How many orders settled?", Asker("U1", {"data"}))
    assert answer.status == "answered"
    assert answer.text == "987,654,321 orders were settled."
    assert not any(str(SECRET_NUMBER) in sent for sent in model.sent)
    assert [s.id for s in answer.sources] == ["T1", "Q1"]


def test_local_model_may_see_values_when_policy_allows(settings, catalog):
    settings.sources.ai.policy.data_values = "local"
    local = ScriptedModel([
        ("run_query", {"sql": "select count(*) from orders", "purpose": "all orders"}),
        ("final_answer", {"answer": "There are 987654321 orders.", "sources": ["Q1"], "confidence": "high",
                          "found": True}),
    ], is_local=True)
    settings.sources.ai.policy.docs = "local"
    brain = Brain(workspace(settings, catalog), ScriptedModel([]), local)
    answer = brain.ask("How many orders?", Asker("U1", {"data"}))
    assert any(str(SECRET_NUMBER) in sent for sent in local.sent)
    assert answer.status == "answered"


def test_blocked_query_is_reported_to_the_model_and_not_run(settings, catalog):
    runner = FakeRunner()
    model = ScriptedModel([
        ("run_query", {"sql": "select full_name from users", "purpose": "names"}),
        ("final_answer", {"answer": "Sorry, personal data can't be shared here.", "sources": [],
                          "confidence": "high", "found": False}),
    ])
    answer = Brain(workspace(settings, catalog, runner), model).ask("List user names", Asker("U1", {"data"}))
    assert runner.sql == []
    assert "personal" in model.sent[-1]
    assert answer.status == "not_found"


def test_people_without_data_access_get_no_data_tools(settings, catalog):
    model = ScriptedModel([
        ("run_query", {"sql": "select count(*) from orders", "purpose": "x"}),
        ("final_answer", {"answer": "You need data access.", "sources": [], "confidence": "high", "found": False}),
    ])
    runner = FakeRunner()
    Brain(workspace(settings, catalog, runner), model).ask("How many orders?", Asker("U2", {"everyone"}))
    assert runner.sql == [] and "not allowed" in model.sent[-1]


def test_sensitive_tables_wait_for_approval(settings, catalog):
    runner = FakeRunner()
    model = ScriptedModel([
        ("run_query", {"sql": "select count(*) from payments where status = 'FAILED'", "purpose": "failed"}),
        ("final_answer", {"answer": "{{Q1}} payments failed.", "sources": ["Q1"], "confidence": "high",
                          "found": True}),
    ])
    answer = Brain(workspace(settings, catalog, runner), model).ask("How many payments failed?", Asker("U1", {"data"}))
    assert answer.status == "waiting_approval" and runner.sql == []
    assert answer.queries["Q1"]["status"] == "waiting_approval"


def test_tool_budget_forces_a_final_answer(settings, catalog):
    settings.sources.limits.max_tool_calls = 2
    model = ScriptedModel([("find_tables", {"query": "orders"})] * 2 + [
        ("final_answer", {"answer": "Not sure.", "sources": [], "confidence": "low", "found": False})])
    answer = Brain(workspace(settings, catalog), model).ask("?", Asker("U1", {"data"}))
    assert answer.tool_calls == 2 and answer.status == "not_found"


def test_tool_calls_written_as_text_are_understood():
    from askcite.brain import text_tool_calls

    allowed = {"final_answer", "run_query"}
    calls = text_tool_calls('Sure! final_answer({"answer": "It {works}", "sources": ["D1"], "found": true})',
                            allowed)
    assert calls[0]["function"]["name"] == "final_answer"
    assert json.loads(calls[0]["function"]["arguments"])["answer"] == "It {works}"
    calls = text_tool_calls('```json\n{"name": "run_query", "arguments": {"sql": "select 1"}}\n```', allowed)
    assert json.loads(calls[0]["function"]["arguments"]) == {"sql": "select 1"}
    assert text_tool_calls("print({'a': 1}) and delete_everything({})", allowed) == []


def test_brain_accepts_a_final_answer_written_as_text(settings, catalog):
    class TextModel(ScriptedModel):
        def chat(self, messages, tools=None, tool_choice=None):
            self.sent.append(json.dumps(messages, default=str))
            return {"role": "assistant", "content": 'final_answer({"answer": "Two retries.", "sources": [], '
                                                    '"confidence": "medium", "found": true})'}

    answer = Brain(workspace(settings, catalog), TextModel([])).ask("Retries?", Asker("U1", {"data"}))
    assert answer.status == "answered" and answer.text == "Two retries."


def test_answers_without_any_lookup_are_sent_back_once(settings, catalog):
    model = ScriptedModel([
        ("final_answer", {"answer": "The minimum is ₹100.", "sources": [], "confidence": "high", "found": True}),
        ("search_code", {"query": "minimum order amount"}),
        ("final_answer", {"answer": "The minimum is ₹1,000.", "sources": [], "confidence": "high", "found": True}),
    ])
    answer = Brain(workspace(settings, catalog), model).ask("Minimum order?", Asker("U1", {"everyone"}))
    assert answer.text == "The minimum is ₹1,000." and answer.tool_calls == 1
    assert "you have not looked anything up" in model.sent[1]
