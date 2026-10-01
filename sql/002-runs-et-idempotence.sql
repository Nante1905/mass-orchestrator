-- Un run à la fois par fil, et une exécution au plus par geste validé.
--
-- Rejoué à chaque démarrage comme le premier script : chaque ordre est
-- idempotent.

-- Le verrou d'un fil : l'heure à laquelle son run en cours a commencé, ou rien.
--
-- Une colonne plutôt qu'un verrou consultatif de Postgres : ce dernier
-- immobiliserait une connexion du pool pendant toute la durée d'un run, jusqu'à
-- plusieurs minutes. Un verrou trop ancien est considéré comme abandonné — un
-- processus arrêté en plein run ne bloque pas le fil pour toujours.
alter table agents.thread
    add column if not exists run_started_at timestamptz;

-- La clé d'idempotence d'une décision : l'appel d'outil qu'elle tranche.
--
-- Un même appel ne peut être décidé qu'une fois. Une reprise rejouée — double
-- clic, deux onglets, reprise après un arrêt du service — retrouve la décision
-- déjà prise au lieu d'exécuter une seconde fois : un courriel envoyé deux fois
-- ne se rattrape pas.
alter table agents.approval_log
    add column if not exists tool_call_id text;

-- Où en est la décision. `executing` est posé **avant** l'appel au serveur ;
-- une ligne restée dans cet état signale une exécution dont l'issue est
-- inconnue, et qui ne doit pas être rejouée à l'aveugle.
alter table agents.approval_log
    add column if not exists status text
        check (status in ('refused', 'executing', 'done', 'failed'));

-- Partiel : les lignes antérieures à ce script n'ont pas de clé.
create unique index if not exists approval_log_call_uidx
    on agents.approval_log (thread_id, tool_call_id)
    where tool_call_id is not null;
