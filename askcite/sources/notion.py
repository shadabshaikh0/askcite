"""Read Notion pages and split them into sections by heading.

Setup (done once by you): create an internal integration in Notion, copy its token
into NOTION_TOKEN in .env, and share the pages Askcite may read (PRDs, TRDs,
runbooks) with that integration. Askcite can only see pages you share.

Each section keeps a link to the exact block, e.g.
https://www.notion.so/<page>#<block>, so answers can point to the right place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

API = "https://api.notion.com/v1"
_HEADINGS = {"heading_1": 1, "heading_2": 2, "heading_3": 3}
_TEXT_BLOCKS = {"paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "quote", "callout",
                "code", "equation"}
_MAX_SECTION_CHARS = 4000


@dataclass
class Section:
    page_id: str
    block_id: str
    page_title: str
    heading_path: list[str]
    url: str
    content: str
    last_edited: datetime | None = None


@dataclass
class NotionPage:
    id: str
    title: str
    url: str
    last_edited: datetime
    parent_id: str | None = None  # parent page id, when the parent is a page


def rich_text(items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in items or [])


def page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return rich_text(prop.get("title")) or "Untitled"
    return "Untitled"


def _block_text(block: dict) -> tuple[str, str]:
    """Return (kind, text) for one block."""
    kind = block.get("type", "")
    body = block.get(kind) or {}
    if kind in _HEADINGS:
        return "heading", rich_text(body.get("rich_text"))
    if kind in _TEXT_BLOCKS:
        text = rich_text(body.get("rich_text"))
        if kind in ("bulleted_list_item", "numbered_list_item"):
            text = f"• {text}"
        elif kind == "to_do":
            text = f"[{'x' if body.get('checked') else ' '}] {text}"
        elif kind == "code":
            text = f"```\n{text}\n```"
        elif kind == "equation":
            text = body.get("expression", "")
        return "text", text
    if kind == "table_row":
        return "text", " | ".join(rich_text(cell) for cell in body.get("cells") or [])
    if kind in ("child_page", "child_database"):
        return "text", f"(sub-page: {body.get('title', '')})"
    if kind in ("bookmark", "link_preview", "embed"):
        return "text", body.get("url", "")
    return "skip", ""


def block_url(page_url: str, block_id: str) -> str:
    return f"{page_url.split('#')[0]}#{block_id.replace('-', '')}"


def sections_from_blocks(page: NotionPage, blocks: list[tuple[dict, int]]) -> list[Section]:
    """Group a page's blocks (block, nesting depth) into sections, one per heading."""
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    current_block_id = page.id
    current_lines: list[str] = []

    def flush():
        text = "\n".join(line for line in current_lines if line.strip()).strip()
        if text:
            headings = [title for _, title in path]
            for start in range(0, len(text), _MAX_SECTION_CHARS):
                sections.append(Section(
                    page_id=page.id, block_id=current_block_id, page_title=page.title, heading_path=headings,
                    url=block_url(page.url, current_block_id), content=text[start: start + _MAX_SECTION_CHARS],
                    last_edited=page.last_edited,
                ))
        current_lines.clear()

    for block, depth in blocks:
        kind, text = _block_text(block)
        if kind == "heading":
            flush()
            level = _HEADINGS[block["type"]]
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, text))
            current_block_id = block["id"]
        elif kind == "text" and text:
            current_lines.append(("  " * depth) + text)
    flush()
    return sections


class NotionClient:
    def __init__(self, token: str, api_version: str = "2022-06-28", transport: httpx.BaseTransport | None = None,
                 min_interval: float = 0.35):
        self.http = httpx.Client(base_url=API, timeout=30, transport=transport, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": api_version, "Content-Type": "application/json",
        })
        self.min_interval = min_interval  # Notion allows about 3 requests per second
        self._last_call = 0.0

    def _request(self, method: str, url: str, **kwargs) -> dict:
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            response = self.http.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(float(response.headers.get("Retry-After", 2 ** attempt)))
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return response.json()

    def pages(self) -> list[NotionPage]:
        """Every page shared with the integration."""
        pages, cursor = [], None
        while True:
            body = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self._request("POST", "/search", json=body)
            pages += [self._page(item) for item in data.get("results", [])
                      if not item.get("archived") and not item.get("in_trash")]
            if not data.get("has_more"):
                return pages
            cursor = data.get("next_cursor")

    @staticmethod
    def _page(item: dict) -> NotionPage:
        return NotionPage(
            id=item["id"], title=page_title(item), url=item.get("url", ""),
            last_edited=datetime.fromisoformat(item["last_edited_time"].replace("Z", "+00:00")),
            parent_id=(item.get("parent") or {}).get("page_id"),
        )

    def me(self) -> dict:
        """The integration's own bot user (used to check the token)."""
        return self._request("GET", "/users/me")

    def page_sample(self, limit: int = 20) -> tuple[list[NotionPage], bool]:
        """Up to `limit` shared pages, and whether there are more."""
        data = self._request("POST", "/search", json={"filter": {"property": "object", "value": "page"},
                                                      "page_size": limit})
        pages = [self._page(item) for item in data.get("results", [])
                 if not item.get("archived") and not item.get("in_trash")]
        return pages, bool(data.get("has_more"))

    def blocks(self, block_id: str, depth: int = 0, max_depth: int = 4) -> list[tuple[dict, int]]:
        """All blocks of a page in reading order, with nesting depth (child pages are not followed)."""
        out, cursor = [], None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._request("GET", f"/blocks/{block_id}/children", params=params)
            for block in data.get("results", []):
                out.append((block, depth))
                if block.get("has_children") and depth < max_depth and block.get("type") not in (
                        "child_page", "child_database"):
                    out.extend(self.blocks(block["id"], depth + 1, max_depth))
            if not data.get("has_more"):
                return out
            cursor = data.get("next_cursor")


def under_roots(pages: list[NotionPage], root_ids: list[str]) -> list[NotionPage]:
    """Keep only the root pages and pages nested below them (using the parents Notion reports)."""
    norm = lambda value: (value or "").replace("-", "")  # noqa: E731
    roots = {norm(r) for r in root_ids}
    parents = {norm(p.id): norm(p.parent_id) for p in pages}
    kept = []
    for page in pages:
        current, seen = norm(page.id), set()
        while current and current not in seen:
            if current in roots:
                kept.append(page)
                break
            seen.add(current)
            current = parents.get(current)
    return kept


@dataclass
class NotionSyncReport:
    pages_seen: int = 0
    pages_indexed: int = 0
    sections: int = 0
    pages_removed: int = 0
    errors: list[str] = field(default_factory=list)
