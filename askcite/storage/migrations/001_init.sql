-- Askcite's own storage. It holds search indexes, the schema list and logs.
-- It never holds rows from your product database.

create extension if not exists pg_trgm;

create table if not exists schema_snapshot (
    id          bigserial primary key,
    source      text not null,
    origin      text not null,              -- 'live' (read from the database) or 'file'
    taken_at    timestamptz not null default now(),
    catalog     jsonb not null
);
create index if not exists schema_snapshot_source_idx on schema_snapshot (source, taken_at desc);

create table if not exists repo_state (
    repo        text primary key,
    commit_sha  text not null,
    indexed_at  timestamptz not null default now()
);

create table if not exists code_chunk (
    id           bigserial primary key,
    repo         text not null,
    commit_sha   text not null,
    path         text not null,
    symbol       text not null,             -- e.g. PaymentService.handleGatewayCallback
    kind         text not null,             -- function | class | object
    start_line   int not null,
    end_line     int not null,
    content      text not null,
    search       tsvector not null
);
create index if not exists code_chunk_search_idx on code_chunk using gin (search);
create index if not exists code_chunk_symbol_trgm_idx on code_chunk using gin (symbol gin_trgm_ops);
create index if not exists code_chunk_repo_path_idx on code_chunk (repo, path);

create table if not exists sql_example (
    id           bigserial primary key,
    repo         text not null,
    commit_sha   text not null,
    path         text not null,
    symbol       text not null,
    start_line   int not null,
    end_line     int not null,
    sql_text     text not null,
    operation    text not null,             -- select | insert | update | delete | other
    tables       text[] not null,
    parsed       boolean not null,
    search       tsvector not null
);
create index if not exists sql_example_tables_idx on sql_example using gin (tables);
create index if not exists sql_example_search_idx on sql_example using gin (search);

create table if not exists doc_section (
    id            bigserial primary key,
    page_id       text not null,
    block_id      text not null,
    page_title    text not null,
    heading_path  text[] not null,
    url           text not null,
    content       text not null,
    last_edited   timestamptz,
    search        tsvector not null,
    unique (page_id, block_id)
);
create index if not exists doc_section_search_idx on doc_section using gin (search);

create table if not exists notion_page_state (
    page_id      text primary key,
    last_edited  timestamptz not null,
    indexed_at   timestamptz not null default now()
);

-- One row per question. Stores the answer *template* (with blanks), never filled-in data values.
create table if not exists question_log (
    id               bigserial primary key,
    asked_at         timestamptz not null default now(),
    slack_user       text,
    channel          text,
    question         text not null,
    answer_template  text,
    sources          jsonb not null default '[]',
    tool_calls       int not null default 0,
    duration_ms      int,
    status           text not null            -- answered | not_found | waiting_approval | error | refused
);

-- One row per database query. Stores the SQL and the row count, never the rows.
create table if not exists query_audit (
    id           bigserial primary key,
    created_at   timestamptz not null default now(),
    question_id  bigint references question_log (id),
    slack_user   text,
    question     text,
    sql_text     text not null,
    tables       text[] not null default '{}',
    status       text not null,               -- blocked | waiting_approval | ok | failed | rejected
    row_count    int,
    duration_ms  int,
    error        text,
    approved_by  text,
    approved_at  timestamptz
);

create table if not exists pending_approval (
    id               bigserial primary key,
    created_at       timestamptz not null default now(),
    audit_id         bigint not null references query_audit (id),
    question         text not null,
    sql_text         text not null,
    answer_template  text not null,
    sources          jsonb not null default '[]',
    slack_user       text,
    channel          text,
    thread_ts        text,
    status           text not null default 'waiting',   -- waiting | approved | rejected
    decided_by       text,
    decided_at       timestamptz
);
