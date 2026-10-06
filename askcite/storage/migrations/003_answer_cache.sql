-- Public demo only: identical questions get the stored answer (the demo data is fake).
create table if not exists answer_cache (
    question_key  text primary key,
    question      text not null,
    answer        jsonb not null,
    created_at    timestamptz not null default now()
);
