"""Read a folder of Markdown documents (PRDs, TRDs, runbooks kept in git or on disk).

Each file is split into sections by heading, like Notion pages, so answers can point
to the exact section (`<web_url>/<file>#<heading>` when a web_url is configured).
"""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime
from pathlib import Path

from askcite.sources.notion import Section

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_MAX_SECTION_CHARS = 4000


def folder_files(root: Path, include: list[str]) -> list[tuple[Path, str]]:
    """(path, path relative to root) for every file matching the include patterns, in a stable order."""
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch("/" + relative, pattern) or
               fnmatch.fnmatch(path.name, pattern) for pattern in include):
            files.append((path, relative))
    return files


def slugify(text: str) -> str:
    """GitHub-style heading anchor: 'Settlement states (T+1)' -> 'settlement-states-t1'."""
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return re.sub(r" ", "-", text)


def sections_from_markdown(page_id: str, relative: str, text: str, web_url: str | None) -> list[Section]:
    lines = text.splitlines()
    title = next((m.group(2) for line in lines if (m := _HEADING.match(line)) and len(m.group(1)) == 1),
                 Path(relative).stem.replace("-", " ").replace("_", " ").title())
    base_url = f"{web_url.rstrip('/')}/{relative}" if web_url else None
    modified = datetime.now(UTC)
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    current_block, current_lines, in_fence = "top", [], False
    used_slugs: dict[str, int] = {}

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if not body:
            return
        anchor = None if current_block == "top" else current_block
        url = (f"{base_url}#{anchor}" if anchor else base_url) if base_url else None
        for start in range(0, len(body), _MAX_SECTION_CHARS):
            sections.append(Section(page_id=page_id, block_id=current_block, page_title=title,
                                    heading_path=[h for _, h in path], url=url,
                                    content=body[start: start + _MAX_SECTION_CHARS], last_edited=modified))

    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading is None:
            current_lines.append(line)
            continue
        flush()
        current_lines = []
        level, heading_text = len(heading.group(1)), heading.group(2).strip()
        while path and path[-1][0] >= level:
            path.pop()
        if level > 1 or heading_text != title:  # the document title itself is not a section heading
            path.append((level, heading_text))
        slug = slugify(heading_text)
        count = used_slugs.get(slug, 0)
        used_slugs[slug] = count + 1
        current_block = slug if count == 0 else f"{slug}-{count}"
    flush()
    return sections
