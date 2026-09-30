# mass-agents

Orchestrateur conversationnel de la plateforme MASS. Un graphe LangGraph à trois
agents spécialisés au-dessus du serveur `mass-mcp`, derrière une façade FastAPI
qui porte seule l'authentification.

Il ne remplace pas le back-office. Ce qu'il ajoute, c'est un chemin
conversationnel vers des gestes qui existent déjà — et il s'arrête là où ces
gestes engagent quelqu'un.

## L'architecture, et pourquoi celle-là

```
  navigateur                mass-agents :4100                     services
  ──────────                ─────────────────                     ────────

  back-office  ──jeton──▶  FastAPI                    ──▶  mass-mcp :4000
   (React)       admin     auth · CORS · SSE                       │
                              │                                    │
                              ▼                        ┌───────────┴────────┐
                        graphe LangGraph               ▼                    ▼
                        (même processus)          mass-backend         Postgres
                              │                    API REST           lecture seule
                              ▼
                        Postgres, schéma `agents`
                        checkpoints · fils · journal


  le graphe
  ─────────
                          ┌──────────────┐
      START ─────────────▶│ superviseur  │◀────────────┐
                          └──────┬───────┘             │
                 ┌───────────────┼───────────────┐     │
                 ▼               ▼               ▼     │
          ┌────────────┐  ┌────────────┐  ┌────────────┴─┐
          │ analyste   │  │ redacteur  │  │ operations   │
          │ lecture    │  │ brouillons │  │ engageant    │
          │ seule      │  │ réversible │  │              │
          └─────┬──────┘  └─────┬──────┘  └──────┬───────┘
                │               │                │
                └───────────────┴────────┐       ▼
                                         │  ┌────────────┐
                                         └─▶│ validation │ ← interrupt()
                                            └─────┬──────┘
                                                  │ resume
                                                  └────────▶ superviseur
```

**Le graphe tourne dans le processus de la façade.** C'est l'écart assumé avec
le plan d'origine, qui prévoyait un LangGraph Server séparé derrière un Express.
La justification du plan était que l'authentification de LangGraph Server est
peu mûre en JavaScript. En Python elle l'est — mais un second serveur à
surveiller, configurer et relayer n'apporte alors plus rien que cette façade ne
fasse déjà. Un processus, un port, et le flux SSE produit là où il est consommé.

Ce que la façade rend, et qui justifie qu'elle existe :

- le même contrôle d'administration que `mass-backend`, secret compris ;
- la maîtrise de la liste CORS, alignée sur celle du backend ;
- le format d'erreur `ApiResponse` que le back-office sait déjà lire ;
- la liberté de changer d'orchestration sans toucher au front.

## Démarrage

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
cp .env.example .env      # puis renseigner SECRET, DB_* et ANTHROPIC_API_KEY
python -m mass_agents     # http://localhost:4100
```

Trois services doivent tourner avant celui-ci : **Postgres**, **`mass-backend`**
(:3000) et **`mass-mcp`** (:4000). Le schéma `agents` et ses tables sont créés
au démarrage — aucune étape manuelle, les scripts de `sql/` sont idempotents et
rejoués à chaque lancement.

`SECRET` doit être **le même que celui de `mass-backend`**, au caractère près.
Un secret différent fait échouer toutes les vérifications avec « session
expirée », ce qui se lit comme un problème d'utilisateur et fait chercher au
mauvais endroit.

Le démarrage est réussi quand le journal montre les quatre lignes :

```
Pools de connexion ouverts
Script appliqué : 001-agents-schema.sql
Checkpointer prêt
mass-agents prêt — MCP http://localhost:4000/mcp, checkpoints dans le schéma agents
```

### Sur Windows : la boucle d'évènements

`python -m mass_agents` s'en occupe. Mais si vous lancez par la commande
`uvicorn`, il faut le lui dire :

```bash
uvicorn mass_agents.api.app:create_app --factory --port 4100 \
        --loop mass_agents.eventloop:selector_loop
```

psycopg refuse de fonctionner en mode asynchrone sur `ProactorEventLoop`, que
Python choisit par défaut sur Windows. Sans cette option, le service démarre,
accepte le port, puis échoue à ouvrir ses pools — et le message parle de boucle
d'évènements là où l'on cherchait une base injoignable. Le contrôle posé dans
`persistence/database.py` transforme cet échec tardif en refus immédiat qui dit
quoi faire.

## Tester

### Obtenir un jeton

L'orchestrateur n'émet aucun jeton : il vérifie ceux du back-office. C'est donc
`mass-backend` qu'on interroge, et une seule fois — le jeton vaut un jour.

```bash
curl -s -X POST http://localhost:3000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@exemple.org","password":"…"}'
# → {"ok":true,"payload":{"accessToken":"eyJ…", …}}
```

Le compte doit avoir le rôle `ADMIN` : le rôle est relu en base à chaque
requête, un compte rétrogradé perd l'agent en même temps que le back-office.

### Avec Postman

Importez `postman/mass-agents.postman_collection.json`. Les variables `email` et
`password` sont à renseigner ; `adminJwt` et `threadId` se remplissent tout
seuls, la première par « 1. Connexion », la seconde par « 3. Ouvrir un fil ».

L'ordre à suivre :

1. **Connexion** — enregistre le jeton, dont toutes les autres requêtes héritent.
2. **Ouvrir un fil** — enregistre `threadId`.
3. **5a / 5b** — une question de lecture, puis une préparation. Rien n'est
   modifié dans le premier cas, rien n'est publié dans le second.
4. **6** — une action engageante. Le flux s'arrête sur `approval_request`.
5. **7** — l'état du fil : `status` vaut `awaiting_approval`, et `pendingApproval`
   porte l'aperçu. Redémarrez le service et rappelez cette route : elle répond la
   même chose.
6. **7a** ou **7b** — approuver (⚠️ envoie de vrais courriels) ou refuser.

Les deux requêtes de run rendent un `text/event-stream`. Postman récent l'affiche
au fil de l'eau ; si votre version le met en tampon, la réponse n'apparaît qu'à la
fin — utilisez `curl -N` pour voir arriver les fragments.

### En ligne de commande

```bash
JETON=eyJ…

# 1. Ouvrir un fil
FIL=$(curl -s -X POST http://localhost:4100/threads \
  -H "Authorization: Bearer $JETON" -H 'Content-Type: application/json' \
  -d '{}' | python -c 'import sys,json; print(json.load(sys.stdin)["payload"]["id"])')

# 2. Un tour de conversation, diffusé
curl -N -X POST http://localhost:4100/threads/$FIL/runs/stream \
  -H "Authorization: Bearer $JETON" -H 'Content-Type: application/json' \
  -d '{"message":"Combien d'\''évènements sont publiés ?"}'

# 3. Après un approval_request : approuver, ou refuser
curl -N -X POST http://localhost:4100/threads/$FIL/runs/resume \
  -H "Authorization: Bearer $JETON" -H 'Content-Type: application/json' \
  -d '{"approved":false,"reason":"mauvais destinataires"}'
```

Un run de lecture ressemble à ceci — deux transferts, deux messages, et le `done`
qui clôt toujours le flux :

```
event: handoff   data: {"to":"analyste"}
event: token     data: {"text":"L'agenda","node":"analyste"}
event: message   data: {"role":"assistant","content":"L'agenda compte 4 évènements…"}
event: handoff   data: {"to":"superviseur"}
event: message   data: {"role":"assistant","content":"L'association compte 4 évènements publiés."}
event: done      data: {"status":"completed","thread_id":"630e4af9-…"}
```

### Vérifier l'isolement du schéma

La garantie du lot 1 se contrôle en une requête : aucune table de ce service ne
doit se trouver dans `public`, où passent les migrations TypeORM.

```sql
select table_schema, table_name from information_schema.tables
where table_name like 'checkpoint%' or table_name in ('thread', 'approval_log');
-- les six lignes doivent toutes être dans le schéma `agents`
```

## Les trois agents

Le découpage est fait par **niveau d'engagement**, pas par domaine métier. Un
agent qui ne détient pas `send_email` ne peut pas l'appeler, quelle que soit la
formulation de la demande.

| Agent | Outils | Effort | Peut-il nuire ? |
|---|---|---|---|
| `analyste` | `list_events`, `get_event_details`, `list_members`, `get_member_details`, `get_attendance_stats`, `get_finance_summary`, `query_analytics` | `high` | Non — aucune écriture atteignable depuis ce nœud |
| `redacteur` | `create_event_draft`, `update_draft`, `create_email_template` + `list_events`, `get_event_details` | `medium` | Écrit, mais tout reste en brouillon et invisible du public |
| `operations` | `mark_attendance`, `send_email` + `get_event_details`, `list_members` | `high` | Oui — d'où le nœud de validation obligatoire en aval |
| `superviseur` | Uniquement les outils de transfert | `low` | Non — il ne fait que router |

Le recouvrement des outils de lecture est voulu : `operations` doit résoudre
« les présents de samedi » en identifiants `EMR…` avant de proposer quoi que ce
soit.

Le même modèle partout ; c'est `output_config.effort` qui différencie. Vérifié
plutôt que supposé : `ChatAnthropic.reasoning_effort` alimente bien
`output_config.effort` dans la requête envoyée à l'API — le risque d'un effort
avalé par l'intégration ne se matérialise pas.

## La validation humaine

C'est le cœur du service. Le déroulé tient en trois temps :

1. L'agent des opérations appelle `send_email` ou `mark_attendance` **sans**
   `confirmed`. `mass-mcp` n'écrit rien et rend l'aperçu.
2. Le nœud `validation` suspend le graphe par `interrupt(aperçu)`. L'état est
   checkpointé — le service peut redémarrer sans que la validation soit perdue.
3. La reprise rejoue **le même** outil avec `confirmed: true`, ou n'appelle rien
   et le dit dans le fil.

**L'aperçu n'est pas reformaté.** Celui de `mass-mcp` est déjà la charge utile :
destinataires résolus, corps intégral, nombre de courriels, liste nominative des
pointages. Le réécrire ferait diverger ce qui est montré de ce qui a été calculé.

**La garantie est double par construction.** Même si le superviseur routait mal,
même si ce nœud était contourné, `mass-mcp` refuserait d'écrire sans `confirmed`.
Le nœud n'est pas la sécurité : il est ce qui permet à la sécurité d'être
franchie légitimement, avec une trace de qui a relu quoi.

## Le jeton, et où il ne va pas

Le jeton de l'administrateur repart **à chaque run**, jamais à la création du
fil et **jamais dans l'état du graphe**.

Le danger est réel et il a été vérifié plutôt que supposé :
`get_checkpoint_metadata` de LangGraph recopie dans les métadonnées persistées
toute valeur scalaire trouvée dans `config["configurable"]`. Un
`configurable["admin_token"] = "eyJ…"` finirait littéralement dans la colonne
`metadata` de la table `checkpoints`, avec la durée de rétention du fil et non
celle du jeton.

Deux parades, posées ensemble parce qu'elles ne protègent pas contre la même
chose :

1. la clé commence par `__`, donc l'exclusion explicite de LangGraph s'applique ;
2. la valeur n'est pas un scalaire mais un objet — seuls `str`, `int`, `bool` et
   `float` sont recopiés, quel que soit le nom de la clé.

La seconde tient même si la première cède. `tests/test_context.py` vérifie la
conjonction sur le vrai code de LangGraph, et démontre au passage ce qui se
passerait sans elle.

## Les routes

| Route | Ce qu'elle fait |
|---|---|
| `GET /health` | Sonde, publique |
| `POST /threads` | Ouvre un fil |
| `GET /threads` | Les fils de l'appelant, du plus récent au plus ancien |
| `GET /threads/{id}/state` | Messages, et aperçu en attente s'il y en a un |
| `POST /threads/{id}/runs/stream` | Un tour de conversation, en SSE |
| `POST /threads/{id}/runs/resume` | Reprend après une validation humaine |

Toutes sauf `/health` exigent le jeton d'administrateur, y compris celles qui ne
font que lire : une conversation avec l'agent porte des noms, des adresses et des
chiffres que rien d'autre ne protège.

Les évènements SSE portent leur type dans le champ `event` : `token`, `message`,
`handoff`, `approval_request`, `error`, `done`. Le flux se termine toujours par
un `done`, y compris après une erreur.

```bash
curl -N -X POST http://localhost:4100/threads/$FIL/runs/stream \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"message":"combien de présents à la Nuit des étoiles ?"}'
```

Sans jeton : 401 avec `WWW-Authenticate: Bearer realm="mass-agents"`.

## Les plafonds

Trois, durs, et posés dès la construction plutôt qu'à un lot de durcissement :
un graphe qui boucle facture un appel de modèle à chaque tour, et personne ne
s'en aperçoit avant la facture.

- `MAX_TURNS` — transferts entre agents sur la vie d'un fil. Contrôlé dans le
  superviseur **avant** l'appel de modèle : un dépassement constaté après coup a
  déjà été payé. Le compteur est dans l'état, donc une reprise ne le remet pas à
  zéro.
- `MAX_TOKENS_PER_THREAD` — jetons cumulés, entrée comprise. Le contexte est
  recompté à chaque tour, comme sur la facture.
- `RUN_TIMEOUT_S` — durée d'un run. Ce qui a été fait est conservé.

Chacun rend un message qui dit quoi faire — reprendre en plus étroit, ouvrir un
nouveau fil — et non une trace technique qui ferait réessayer à l'identique.

## Ce qui est tracé

`agents.approval_log`, et rien d'autre. Les lectures ne regardent personne et
les brouillons se défont ; ce qui mérite une trace est ce qu'un humain a
approuvé.

L'aperçu y est conservé intégralement, pas seulement les arguments : six mois
plus tard, `group_id: G0003` ne dit pas à qui le courriel est parti, alors que
l'aperçu porte les adresses résolues et le corps. Les refus sont enregistrés au
même titre que les approbations — c'est ce qui distingue « refusé » de « jamais
demandé ».

## Structure

```
src/mass_agents/
  config.py              configuration validée au démarrage, groupée par responsabilité
  __main__.py            point d'entrée
  eventloop.py           la boucle que psycopg exige sur Windows
  domain/                vocabulaire métier et erreurs — aucune dépendance d'infrastructure
    actions.py           PendingAction, ApprovalRequest/Decision, outils engageants
    errors.py            ce que le service distingue : corrigeable ou non
  auth/identity.py       vérification du jeton + relecture du rôle en base
  tools/
    catalog.py           la répartition des outils, en données
    toolset.py           l'outillage d'un run — jamais au niveau du module
  agents/
    models.py            le modèle décliné par effort
    prompts.py           les prompts, regroupés pour se relire côte à côte
    specialists.py       les trois agents, décrits par une ligne de données
    handoff.py           les outils de transfert du superviseur
  graph/
    state.py             quatre champs, et surtout pas le jeton
    context.py           ce qu'un run transporte — et la parade contre la fuite
    limits.py            les plafonds : mesurer et formuler, pas décider
    pending.py           reconnaître une écriture en attente (fonction pure)
    nodes/               superviseur, spécialistes, validation
    builder.py           l'assemblage, compilé une fois
    runtime.py           le service : ouvrir, avancer, reprendre
    events.py            le vocabulaire du flux
  api/                   façade FastAPI : auth, CORS, ApiResponse, SSE
  persistence/           pools, schéma, fils, journal — le seul endroit avec du SQL
sql/                     le schéma `agents`
postman/                 collection prête à importer
tests/                   54 tests, sans base ni réseau ni modèle
```

Les frontières sont nettes et c'est délibéré : `domain/` ne connaît aucune
infrastructure, `graph/` ne connaît pas HTTP, `api/` ne connaît pas LangGraph, et
seul `persistence/` écrit du SQL. C'est ce qui permet de tester la validation
humaine de bout en bout — `interrupt()` et `Command(resume=…)` compris — sans
monter autre chose qu'un checkpointer en mémoire.

## Vérifier

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
```

La suite ne joint ni base, ni serveur MCP, ni API de modèle. Ce qui demande du
réseau est remplacé par la doublure la plus mince qui rende le test significatif.

## Ce qui reste

- **Granularité du streaming** — les jetons sont diffusés jusqu'au navigateur
  (`token`), et les messages complets aussi (`message`). Un client simple peut
  ignorer les premiers sans rien perdre. À trancher à l'usage si le coût du
  relais fin ne se justifie pas.
- **Éprouver le routage** — les tests couvrent la traduction d'un choix en
  destination, pas la qualité du choix. Elle se mesure sur des demandes réelles.
- **Mesurer avant d'optimiser l'effort** — `low` sur le routage est un pari
  raisonnable, pas une mesure. Un superviseur qui route mal coûte un tour
  complet, ce qui est plus cher que l'appel économisé.
