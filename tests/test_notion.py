import json
from datetime import UTC, datetime

import httpx

from askcite.sources.notion import NotionClient, NotionPage, sections_from_blocks, under_roots


def block(kind, text, block_id):
    return {"id": block_id, "type": kind, kind: {"rich_text": [{"plain_text": text}]}, "has_children": False}


PAGE = NotionPage(id="page-1", title="Settlement TRD", url="https://www.notion.so/Settlement-TRD-page1",
                  last_edited=datetime(2026, 9, 1, tzinfo=UTC))


def test_sections_follow_headings_and_link_to_blocks():
    blocks = [(block("paragraph", "Intro text", "b-0"), 0),
              (block("heading_1", "Process", "b-1"), 0),
              (block("paragraph", "Orders settle on T+1.", "b-2"), 0),
              (block("heading_2", "States", "b-3"), 0),
              (block("bulleted_list_item", "SETTLED means money received", "b-4"), 0),
              (block("heading_1", "Failures", "b-5"), 0),
              (block("paragraph", "Retry twice.", "b-6"), 0)]
    sections = sections_from_blocks(PAGE, blocks)
    assert [s.heading_path for s in sections] == [[], ["Process"], ["Process", "States"], ["Failures"]]
    assert sections[2].content == "• SETTLED means money received"
    assert sections[2].url == "https://www.notion.so/Settlement-TRD-page1#b3"


def test_client_pages_and_blocks_with_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            body = json.loads(request.content)
            if "start_cursor" not in body:
                return httpx.Response(200, json={"results": [{
                    "object": "page", "id": "p1", "url": "https://notion.so/p1",
                    "last_edited_time": "2026-09-01T00:00:00Z",
                    "properties": {"Name": {"type": "title", "title": [{"plain_text": "TRD"}]}},
                    "parent": {"type": "workspace"}}], "has_more": True, "next_cursor": "c2"})
            return httpx.Response(200, json={"results": [], "has_more": False})
        return httpx.Response(200, json={"results": [block("paragraph", "hello", "b1")], "has_more": False})

    client = NotionClient("secret", transport=httpx.MockTransport(handler), min_interval=0)
    pages = client.pages()
    assert [p.title for p in pages] == ["TRD"]
    assert client.blocks("p1")[0][0]["id"] == "b1"


def test_under_roots_keeps_only_nested_pages():
    def page(pid, parent):
        return NotionPage(id=pid, title=pid, url="", last_edited=PAGE.last_edited, parent_id=parent)
    pages = [page("root", None), page("child", "root"), page("grandchild", "child"), page("other", None)]
    assert [p.id for p in under_roots(pages, ["root"])] == ["root", "child", "grandchild"]
