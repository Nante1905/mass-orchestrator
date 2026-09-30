-- Schéma réservé à l'orchestrateur.
--
--   psql -h localhost -p 5433 -U postgres -d mass_db_seed_test -f sql/001-agents-schema.sql
--
-- Il vit dans la même base que MASS, mais hors de `public` : les migrations
-- TypeORM de `mass-backend` ne connaissent que `public`, et une table de
-- conversation qu'elles croiseraient un jour serait supprimée sans préavis.
--
-- Les quatre tables du checkpointer (`checkpoints`, `checkpoint_blobs`,
-- `checkpoint_writes`, `checkpoint_migrations`) ne sont pas créées ici : c'est
-- `AsyncPostgresSaver.setup()` qui s'en charge au démarrage, dans ce schéma,
-- parce que leur définition suit la version de LangGraph et non la nôtre.

create schema if not exists agents;

-- Propriété et index des conversations.
--
-- Le checkpointer sait retrouver l'état d'un fil à partir de son identifiant,
-- mais il ignore à qui ce fil appartient. Sans cette table, un administrateur
-- qui devinerait un identifiant lirait la conversation d'un autre — et
-- `GET /threads` n'aurait rien à lister.
create table if not exists agents.thread (
    id text primary key,
    owner_user_id text not null,
    title text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists thread_owner_updated_idx
    on agents.thread (owner_user_id, updated_at desc);

-- Journal des écritures engageantes, et d'elles seules.
--
-- Ce qui est tracé ici n'est pas « ce que l'agent a fait » — les lectures ne
-- regardent personne et les brouillons se défont. C'est ce qu'un humain a
-- approuvé : quel outil, sur quel aperçu, et qui l'a relu. L'aperçu est
-- conservé intégralement parce que c'est lui qui a été validé, pas les
-- arguments : c'est la seule pièce qui permette de dire, six mois plus tard, ce
-- que la personne avait sous les yeux.
create table if not exists agents.approval_log (
    id bigserial primary key,
    thread_id text not null,
    decided_by_user_id text not null,
    decided_by_email text,
    tool_name text not null,
    approved boolean not null,
    reason text,
    preview jsonb not null,
    arguments jsonb not null,
    outcome jsonb,
    decided_at timestamptz not null default now()
);

create index if not exists approval_log_thread_idx
    on agents.approval_log (thread_id, decided_at desc);
