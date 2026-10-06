# Changelog

## Unreleased
- **Public demo mode** (`askcite run --public-demo`):
  - anyone can try `/ask` and see a read-only Connectors page, while admin pages keep the password;
  - limits per visitor and per day, one question at a time, and a maximum question length;
  - suggested questions are pre-answered and cached, so they are instant and use no AI quota.
- **"How Askcite found this"**: each answer lists the search steps it took, without any data values.
- Cleaner answers: no "Final answer:" prefixes or source ids in the text.
- `deploy/demo/`: Docker Compose with Postgres, the app and Caddy (automatic HTTPS). There is a free hosting
  guide for Oracle Cloud and Gemini.
- Cloud models get automatic retries on rate limits and use the provider's default temperature.
- Model errors are logged on the server and never shown to public visitors.

## v0.1.0
First public version.

- Ask in **Slack** (or the web page); answers come from **code**, **documents** and a **live read-only database**,
  with links to the exact commit and lines, document section or query.
- **Connectors** web page and `askcite connect` for Slack, Notion, any git host, PostgreSQL and docs folders:
  test the connection, save it (secrets encrypted), see sync status.
- **Safety**: single read-only `SELECT` only, personal/secret columns blocked, read-only database user verified,
  time and row limits, audit log without rows, approvals for sensitive tables.
- **Privacy**: query results never go to a cloud AI model; answers are written with blanks that Askcite fills in.
- Code understanding for Kotlin and Java (tree-sitter), including SQL written inside strings.
- Works with local models (Ollama) or cloud models (Claude, OpenAI, Gemini… via LiteLLM).
- `askcite demo setup`: a fake demo shop to try everything in a few minutes; 20-question benchmark.
