# Recording the demo (GIF + 2-minute video)

Use **only the demo shop**. It's fake data, safe to publish.

## Setup
```bash
askcite demo setup
ASKCITE_CONFIG_DIR=examples/demo-shop/config askcite run
```
Optional: connect a test Slack workspace (see [slack-app.md](slack-app.md)) to show the Slack flow.

## Script (about 2 minutes)
1. **Connectors page** (15 s): show the cards for the code repo, the docs folder and the database, all
   *Connected*. Open the database card's **Test**: "✓ Connected", read-only verified.
2. **Connect a source** (20 s): click *Docs folder → + Add*, point it at `examples/demo-shop/docs`,
   **Test** ("Found 4 documents"), then **Save & sync**.
3. **A code question** (25 s): in *Try a question* (or Slack), ask *"What happens when a payment fails?"*.
   Show the answer and the **code and runbook sources**.
4. **A data question** (25 s): ask *"How many orders are in each status right now?"*. Open the query to show
   the SQL that was run.
5. **Safety** (20 s): ask *"What is the phone number of the customer who placed order 12?"*. It's refused.
   Then run `askcite check-sql "delete from orders"` in a terminal and show it's blocked.
6. **Close** (10 s): the benchmark table and the GitHub link.

## Tips
- Use a cloud model for the recording (e.g. `cloud_model: anthropic/claude-sonnet-5` in
  `examples/demo-shop/config/sources.yaml`). Answers come back in a few seconds, versus about 40 s locally.
- Record at 1280×800. Use [Kap](https://getkap.co) or QuickTime on macOS, and export the GIF at 12 fps.
- The README screenshots live in `docs/images/` (`answer-*.png`, `connectors.png`, `connector-postgres.png`,
  `cli.png`, `public-demo.png`). Save a recording as `docs/images/demo.gif`.
