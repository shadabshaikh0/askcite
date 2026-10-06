-- Connectors added from the web page or `askcite connect`. Secrets are stored encrypted (Fernet).
create table if not exists connector (
    id          bigserial primary key,
    kind        text not null,                  -- slack | notion | git | postgres | folder
    name        text not null unique,           -- also the source name used in links and the index
    config      jsonb not null default '{}',    -- non-secret settings
    secrets     text,                           -- encrypted JSON of secret fields; never shown again
    enabled     boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- One row per sync of one source (code repo, Notion, docs folder, schema), for the status page.
create table if not exists sync_run (
    id           bigserial primary key,
    source       text not null,                 -- e.g. git:shop-backend, notion, folder:docs, schema:shop-db
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text not null,                 -- running | ok | failed
    stats        jsonb not null default '{}',
    error        text
);
create index if not exists sync_run_source_idx on sync_run (source, started_at desc);

-- Which version of each Markdown file is indexed (so unchanged files are skipped).
create table if not exists docs_file_state (
    page_id       text primary key,             -- folder:<name>/<relative path>
    content_hash  text not null,
    indexed_at    timestamptz not null default now()
);

-- Small key/value settings, e.g. the hashed admin password for the web page.
create table if not exists app_setting (
    key         text primary key,
    value       text not null,
    updated_at  timestamptz not null default now()
);
