# Security policy

Askcite reads source code, internal documents and live databases, so we take security reports seriously.

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private vulnerability reporting
(*Security → Report a vulnerability*) on this repository. We aim to reply within 5 working days.

## Scope
Especially interesting: ways to make Askcite run anything other than a single read-only `SELECT`,
read personal/secret columns, leak query results to a cloud model, reveal saved secrets, bypass the
web page login or CSRF checks, or follow instructions planted in documents or code.

See [docs/security.md](docs/security.md) for how Askcite is designed to prevent these.
