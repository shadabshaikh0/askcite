# How Askcite keeps your data safe

Askcite connects an AI model to three sensitive things: **source code**, **internal documents** and a
**live database**. This page explains the risks and the defences, layer by layer.

## Threat model

| Risk | Example | Defence (below) |
|---|---|---|
| The AI changes data | "delete old orders", or a buggy query | Layers 1–4 |
| The AI reads personal data | "show me this customer's phone number" | Layer 3 |
| Production data leaks to an AI provider | Query results sent to a cloud model | Layer 6 |
| Prompt injection | A document says "ignore your rules and run …" | Layers 2–5, 7 |
| Expensive or slow queries | A `pg_sleep`, or a huge join on the primary | Layers 1, 4 |
| Leaked credentials | Tokens or passwords visible on screen or in logs | Layer 8 |
| Someone else uses the admin page | A colleague's browser or a malicious website | Layer 9 |
| Wrong people ask about data | Anyone in Slack querying the database | Layer 10 |

## Layer 1: a read-only database user (checked when you connect)
Askcite is meant to use a **read-only user on a read replica**. The Postgres connector's **Test** checks this.
It **refuses to save** a connection whose user is a superuser, owns tables, or has INSERT, UPDATE, DELETE or TRUNCATE
rights, unless you explicitly tick *allow anyway*. A hot-standby replica passes automatically, because writes
are impossible there.

## Layer 2: every query is checked before it runs (`askcite/safety/sql_guard.py`)
Parsed with [sqlglot](https://github.com/tobymao/sqlglot), not with regular expressions:
- exactly **one** statement, and it must be a `SELECT` (WITH and UNION are fine);
- nothing that writes, anywhere in the tree: no INSERT, UPDATE, DELETE or MERGE, no DDL, no COPY, no
  `SELECT … INTO`, no `FOR UPDATE`, and no data-modifying CTEs;
- no dangerous functions (`pg_sleep`, file access, `dblink`, `query_to_xml`, `set_config`, advisory locks, …);
- only tables that exist in the connected schema; never system catalogs; never tables you block.

`askcite check-sql "<sql>"` shows the verdict for any query.

## Layer 3: personal and secret columns cannot be queried (`askcite/safety/pii.py`)
Columns such as names, phone numbers, email, PAN or Aadhaar, bank accounts, addresses, dates of birth,
passwords, tokens and OTPs are detected by name. A query that touches them **anywhere** is blocked, not just in
the SELECT list. So are `SELECT *` and whole-row tricks like `row_to_json(u)` on tables that have them.
The AI is told which columns are hidden, so it can explain that personal data can't be shared.
You can add columns (`extra_sensitive_columns`) or allow false positives.

## Layer 4: limited, read-only execution (`askcite/data/runner.py`)
Queries run in a **read-only transaction** with a **statement timeout** (default 15 s). Rows are streamed from a
server-side cursor, which itself only accepts SELECT, and reading stops at the **row limit** (default 500).

## Layer 5: audit without data, and optional approvals
Every query attempt is logged: who asked, the question, the SQL, the tables, and blocked/ok/failed with the row
count. **Rows are never stored.** The question log keeps the answer *template* (with blanks), not the
numbers. Queries on tables listed in `approval_tables` wait until an approver clicks **Run it** in Slack.

## Layer 6: data values never reach a cloud AI
When the answering model is a cloud model, it **never sees query results**. It writes the SQL from table and column
names, plus an answer with blanks:

```
{{Q1}} orders were settled yesterday.
```

Askcite runs the query and fills the blanks **locally**. Database error messages that could quote a stored value
are scrubbed before the model sees them. Only a model you run yourself (e.g. Ollama) may read values, and only
when `ai.policy.data_values: local`. This is a structural rule, covered by a test that checks the model never
received a known value.

## Layer 7: content is information, never instructions
Documents, code and data are passed to the model as tool results, and the system prompt tells it to treat
them as information only. More importantly, there is **nothing dangerous for an injected instruction to call**:
- every tool is read-only;
- SQL goes through layers 1–4;
- the AI cannot send messages anywhere except the reply to the question it was asked;
- tool calls, queries and time are capped per question (default 8 tool calls, 2 queries, 60 s).

## Layer 8: secrets are encrypted and never shown again
Connector secrets (tokens, passwords, SSH keys) are encrypted with Fernet (AES + HMAC). The key comes from
`ASKCITE_SECRET_KEY`, or is generated once into `<data dir>/secret.key` with owner-only permissions.
- Forms never display saved secrets; they show *"saved — leave empty to keep"*.
- Secrets are excluded from object representations, so they don't end up in logs.
- A git SSH key is written to a private temporary file only while git runs. An HTTPS token reaches git through
  `GIT_ASKPASS`, never in URLs or command lines, and is redacted from error messages.

## Layer 9: the admin web page is locked down
- It listens on **127.0.0.1** by default.
- Every page needs the **admin password**. That's `ASKCITE_ADMIN_PASSWORD`, or one generated on first start
  that's printed once and stored as a salted scrypt hash.
- Every POST needs a **CSRF token** (derived from the secret key), and cross-origin requests are refused.
- Put it behind your SSO proxy if you expose it beyond localhost.

## Layer 10: who may ask what
The Slack bot answers only in allowed channels and DMs. Database questions need the `data` group, and
approvals need `approvers`. Groups come from `access.yaml` and from the Slack connector, and can map to Slack
user groups.

## Known limitations
- Personal-column detection is name-based. Review the blocked columns for your schema, and add
  `extra_sensitive_columns` for anything unusual.
- Counting rows by a personal attribute can still reveal aggregate information. Keep the database user's
  grants as narrow as you need.
- Documents and code are sent to the configured model. If that's a cloud model, choose one your company is
  allowed to use, or run a local model.
- Askcite does not mirror per-document permissions from Notion or git hosts. Everyone allowed to ask can get
  answers from every connected source. Connect only what that audience may see.
