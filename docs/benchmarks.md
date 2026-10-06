# Benchmarks

Askcite ships a test-question runner. The demo shop has 20 questions covering business rules in code,
documents, live data (checked against the expected SQL on the fake database) and personal-data refusals.

## Run it

```bash
askcite demo setup
ASKCITE_CONFIG_DIR=examples/demo-shop/config askcite eval examples/demo-shop/config/questions.yaml \
  --out eval-report.json --markdown results.md
```

It reports, per model:

| Metric | Meaning |
|---|---|
| Answered | Questions answered (not "not found" or errors) |
| Right source cited | The answer cites the expected file or document |
| Right tables | Data questions used the expected tables |
| Same numbers as expected | The query returned the same rows as the expected SQL |
| Mentions key facts | The answer contains the expected key facts (e.g. "₹1,000") |
| Refused personal data | Questions asking for personal data were refused |
| Avg time, avg tool calls | Speed and effort per question |

## Results

*Coming soon.* Results for a local model (`qwen2.5:7b` via Ollama) and a cloud model will be added here.
Run the command above to measure your own model, and feel free to send a PR with the table.

Notes from early runs with `qwen2.5:7b` on a laptop (M2, 16 GB):
- About 20–150 seconds per question.
- Small local models sometimes write tool calls as text, run queries the question didn't need, or try to answer
  without looking anything up. Askcite handles the first and refuses the last, but a stronger model gives
  clearly better answers.
