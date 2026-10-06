"""Public demo mode: open read-only pages, limits, cached answers (against the separate test database)."""

import pytest
from fastapi.testclient import TestClient

from askcite.brain import Answer
from askcite.config import DemoSettings
from askcite.runtime import Runtime
from askcite.tools import Source
from askcite.web.app import create_app, warm_cache
from askcite.web.auth import csrf_token
from askcite.web.limits import QuestionLimiter

pytestmark = pytest.mark.integration
AUTH = ("admin", "test-password-123")


class CountingBrain:
    def __init__(self):
        self.questions = []

    def ask(self, question, asker, channel=None):
        self.questions.append(question)
        return Answer(question, "answered", f"Answer to: {question}", confidence="high", tool_calls=2,
                      sources=[Source("C1", "code", "PaymentService.kt › handle", "https://x/y", "lines 1–9")],
                      steps=[{"tool": "search_code", "detail": "Searched the code for “payment” — 3 found"}])


@pytest.fixture()
def runtime(settings, store, test_store_url, tmp_path, monkeypatch):
    monkeypatch.setenv("ASKCITE_ADMIN_PASSWORD", AUTH[1])
    settings = settings.model_copy(update={"data_dir": tmp_path / "data", "config_dir": tmp_path,
                                           "store_url": test_store_url})
    settings.sources.database = None
    settings.sources.demo = DemoSettings(suggested_questions=["What happens when a payment fails?"],
                                         source_url="https://github.com/example/askcite", per_visitor_limit=2,
                                         daily_limit=3, max_question_chars=60)
    rt = Runtime(settings, store=store)
    rt._brain = CountingBrain()
    return rt


@pytest.fixture()
def client(runtime):
    return TestClient(create_app(runtime, public_demo=True))


def ask(client, runtime, question, **kwargs):
    token = csrf_token(runtime.box.derive(b"csrf"))
    return client.post("/ask", data={"csrf": token, "question": question}, **kwargs)


def test_visitors_see_read_only_pages_and_admin_stays_locked(client, runtime):
    ask_page = client.get("/ask")
    assert ask_page.status_code == 200 and "all data is made up" in ask_page.text
    assert "What happens when a payment fails?" in ask_page.text  # suggested question
    page = client.get("/connectors")
    assert page.status_code == 200 and "read-only" in page.text
    assert "Sync all now" not in page.text and "+ Add" not in page.text and ">Connect<" not in page.text
    token = csrf_token(runtime.box.derive(b"csrf"))
    assert client.get("/connectors/new/folder").status_code == 401
    assert client.post("/connectors/save", data={"csrf": token, "kind": "folder"}).status_code == 401
    assert client.post("/sync", data={"csrf": token}).status_code == 401
    assert client.get("/login").status_code == 401
    admin_view = client.get("/connectors", auth=AUTH)
    assert "Sync all now" in admin_view.text


def test_answers_show_steps_and_repeat_questions_come_from_the_cache(client, runtime):
    first = ask(client, runtime, "What happens when a payment fails?")
    assert "Answer to: What happens when a payment fails?" in first.text
    assert "How Askcite found this" in first.text and "Searched the code" in first.text
    second = ask(client, runtime, "what happens when a payment FAILS")  # same question, other spelling
    assert "instant (answered before)" in second.text and len(runtime._brain.questions) == 1


def test_limits_per_visitor_daily_and_length(client, runtime):
    ask(client, runtime, "Question one?")
    ask(client, runtime, "Question two?")
    third = ask(client, runtime, "Question three?")
    assert "try again in about" in third.text and len(runtime._brain.questions) == 2
    assert "under 60 characters" in ask(client, runtime, "x" * 61).text
    assert "Answer to: Question three?" in ask(client, runtime, "Question three?", auth=AUTH).text  # admin: no limit


def test_error_details_are_hidden_from_visitors(client, runtime):
    class FailingBrain:
        def ask(self, question, asker, channel=None):
            failed = Answer(question, "error", "Sorry — something went wrong while looking this up.")
            failed.how = "AuthenticationError: API key not valid"
            return failed

    runtime._brain = FailingBrain()
    visitor = ask(client, runtime, "What happens when a payment fails?")
    assert "something went wrong" in visitor.text and "API key not valid" not in visitor.text
    assert "API key not valid" in ask(client, runtime, "What happens when a payment fails?", auth=AUTH).text


def test_daily_limit_and_busy_message():
    clock = [1_000_000.0]
    limiter = QuestionLimiter(per_visitor=100, window_minutes=10, daily_limit=2, clock=lambda: clock[0])
    for visitor in ("a", "b"):
        assert limiter.refusal(visitor) is None
        limiter.record(visitor)
    assert "all the questions it can for today" in limiter.refusal("c")
    clock[0] += 86400  # next day
    assert limiter.refusal("c") is None
    assert limiter.try_start() and not limiter.try_start()
    limiter.finish()
    assert limiter.try_start()


def test_warm_cache_answers_suggested_questions_once(runtime):
    pauses = []
    assert warm_cache(runtime, ["Q1?", "Q2?"], pause_seconds=7, sleep=pauses.append) == 2
    assert warm_cache(runtime, ["Q1?", "Q2?"], pause_seconds=7, sleep=pauses.append) == 0
    assert runtime._brain.questions == ["Q1?", "Q2?"] and pauses == [7, 7]
