from __future__ import annotations

import httpx

from askcite.config import NotionSource, Settings, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult
from askcite.sources.notion import NotionClient


class NotionConnector(Connector):
    kind = "notion"
    title = "Notion"
    description = "PRDs, TRDs and runbooks. Askcite only sees pages you share with the integration."
    transport: httpx.BaseTransport | None = None  # tests replace this

    fields = [
        Field("token", "Integration token", "password", required=True, secret=True, placeholder="ntn_… / secret_…",
              help="notion.so/my-integrations → New integration → copy the Internal Integration Secret. "
                   "Then on each page: ⋯ → Connections → add the integration."),
        Field("root_page_ids", "Only these pages (optional)", "list",
              help="Page IDs to limit indexing to, including their sub-pages. Empty = every shared page."),
    ]

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        client = NotionClient(secrets.get("token") or "", transport=self.transport, min_interval=0)
        try:
            me = client.me()
            pages, more = client.page_sample(limit=20)
        except httpx.HTTPStatusError as error:
            reason = "invalid token" if error.response.status_code == 401 else f"HTTP {error.response.status_code}"
            return TestResult(False, f"Notion rejected the request ({reason})")
        except httpx.HTTPError as error:
            return TestResult(False, f"Could not reach Notion: {error}")
        count = f"{len(pages)}{'+' if more else ''}"
        result = TestResult(True, f"Connected as “{me.get('name') or 'integration'}” — {count} page(s) shared",
                            details=[p.title for p in pages[:5]])
        if not pages:
            result.warnings.append("No pages are shared yet: open a page → ⋯ → Connections → add the integration.")
        return result

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        sources.notion = NotionSource(token=secrets.get("token"), root_page_ids=config.get("root_page_ids") or [],
                                      origin="connector")

    def source_keys(self, name: str) -> list[str]:
        return ["notion"]
