# Pink_skin Bot — Plan d'implémentation

> Bot Telegram type **Rose/Marie**, multi-tenant, conçu pour être "vendu" groupe par
> groupe. **Un seul bot / un seul token BotFather** (`@Pink_skin_bot`), isolation par
> `chat_id`. Statut : plan figé en phase ANALYSE (juin 2026), pas encore implémenté.
>
> Principe directeur : **6 chantiers d'infra génériques** (core, réutilisables par tout
> bot) + **un flow applicatif** `telegram/pink_skin` qui se construit par-dessus sans
> rien ajouter au core.

---

## Partie I — Infra générique (core)

### Chantier A — Receiver : événements de modération
**Fichiers** : `services/telegram_bot_service.py`, `tasks/io/telegram_receiver.py`

1. `allowed_updates` configurable sur `TelegramBotService` (`get_parameter_schema`),
   défaut `["message","callback_query"]` (**back-compat**). Remplacer les 2 hardcodes :
   `telegram_bot_service.py:127` (service) **et** `:494` (pool). ⚠️ Telegram n'envoie
   `chat_member` que s'il est explicitement demandé.
2. `telegram_receiver.py:_on_update` :
   - **Nouvelle branche** pour updates sans message : `my_chat_member` / `chat_member`
     (actuellement `:108-110` `return` si pas de message → tout perdu). Surfacer :
     `telegram.update_kind`, `telegram.new_status`, `telegram.old_status`,
     `telegram.actor_id` (`from.id`).
   - Enrichir la branche message (additif) : `telegram.chat_type`,
     `telegram.reply_to_user_id`, `telegram.reply_to_message_id`,
     `telegram.new_chat_members` (CSV), `telegram.left_chat_member`,
     `telegram.migrate_to_chat_id`, `telegram.entities` (JSON).
   - Toujours exposer `telegram.raw` (update brut JSON).

**Tests** : étendre `tests/test_telegram.py` (parsing my_chat_member, join, reply, migration).

### Chantier B — Task générique `telegramApi`
**Fichiers** : `services/telegram_bot_service.py`, nouveau `tasks/io/telegram_api.py`

1. Exposer `call_api(method, params)` public sur `TelegramBotService` (wrap `_api_call`) ;
   idem côté pool via `_api_call_static`.
2. Task `telegramApi` (`TYPE="telegramApi"`) : params `service_id`, `method`, `params`
   (JSON templaté) → réponse Bot API en JSON dans le FlowFile + `telegram.api_ok`.
   Symétrique d'`executeSQL`. **Aucun verbe métier (ban/mute/...) dans le core** — ils
   vivent dans le flow.

**Tests** : `tests/test_telegram_api_task.py` (mock call_api).

### Chantier C — `get_service` dans executeScript
**Fichiers** : `tasks/system/execute_script.py`

- Injecter `local_ns['get_service'] = lambda sid: self._services.get(sid)` (calque de `fs`,
  `:218`). **Résolution exclusive via `self._services`** (services déclarés du flow ;
  aucun registre global, aucun accès cross-flow/cross-user — `base_task.py:267`).
- Sert aussi à `embed_llm` (aiban) et `group_db` (SQL).

**Tests** : `tests/test_execute_script_services.py` (service du flow OK, non déclaré → None).

### Chantier 1 — Tasks SQL via `dbConnectionPool`
**Fichiers** : `tasks/data/execute_sql.py`, `services/db_connection_pool.py`

- `executeSQL`/`putSQL` : ajouter `service_id` → `self.get_service` →
  `service.execute_query/execute_update` (SQLite **et** PostgreSQL). `db_path` conservé en
  fallback SQLite legacy (aucun consommateur trouvé → non bloquant).

**Tests** : `tests/test_execute_sql.py` (sqlite via service).

### Chantier 2 — Placeholders nommés (anti-injection) — *dépend de 1*
**Fichiers** : `services/db_connection_pool.py`, `tasks/data/execute_sql.py`

- Helper backend-aware sur le service : SQLite garde `:name` natif ; psycopg2 réécrit
  `:name`→`%(name)s`.
- `putSQL` : **supprimer** `sql.replace('${content}', ...)` (`:92`). Nouveau contrat : SQL
  avec `:name`, params construits depuis attributs/contenu FlowFile, **liés** (jamais concaténés).

**Tests** : translation `:name`→`?`/`%(name)s` ; valeur hostile (`'; DROP TABLE`) inerte.

### Chantier D — `startupTrigger` (source one-shot)
**Fichiers** : nouveau `tasks/system/startup_trigger.py`

- Micro-task source (`TYPE="startupTrigger"`) qui émet **un seul** FlowFile au démarrage
  puis plus rien — calque de `cronTrigger.is_persistent_source` / `has_pending_input`
  (l'engine gère déjà les root tasks one-shot : `continuous_executor.py:520-530,1336`).
  Générique/réutilisable. Sert ici à déclencher le script d'init DB.

**Tests** : émet une fois, puis `has_pending_input()` False.

### Ordre & dépendances
```
A ┐  B ┐  C ┐  D ┐    (parallélisables)
1 ─→ 2              (2 dépend de 1)
      └────────────► Partie II (flow) dépend de A,B,C,D,1,2
```

---

## Partie II — Application : flow `telegram/pink_skin` v1.0.0

### II.1 Topologie ROUTER
```
telegramReceiver → classify (executeScript: route selon update_kind/chat_type/commande)
   ├─(membership)→ onboarding_script → telegramSend
   ├─(command)───→ command_script    → telegramSend
   ├─(message)───→ moderation_script  → telegramSend
   └─(dm)────────→ dm_script          → telegramSend
startupTrigger → init_script
cron(×3)       → sweep_restrictions / sweep_subscriptions / sweep_repcache
```
Chaque sous-script isolé et testable ; `classify` ne fait que router.

### II.2 Services du flow
- `telegram_bot` (`telegramBot`, token + `allowed_updates` incluant `chat_member`).
- `group_db` (`dbConnectionPool`, `db_type=postgresql`, **pgvector**).
- `embed_llm` (`llmConnection`, `embedding_model` épinglé) — aiban runtime.
- `mod_llm` (`llmConnection`) — validation/catégorisation au `/aiban` uniquement.

### II.3 aiban — similarité vectorielle (PAS de LLM par message)
**Apprentissage (`/aiban` en reply)** : `mod_llm` valide "vrai spam ?" + catégorise →
`embed_llm.embed([texte])` → vecteur stocké.
**Runtime (par message, gaté)** : `embed_llm.embed([msg])` → nearest-neighbor cosine →
si `sim ≥ aiban_threshold` → action `group_config.aiban_action ∈ {ban,mute}`.

**Gate** (`group_config.aiban_gate`) : défaut `risky` (embedder si membre récent/peu actif
**OU** lien/mention/forward/média) ; `all` = tout scanner. Les habitués qui papotent =
skip, zéro embedding.

**3 couches anti-spam runtime** (gratuit→cher) :
`locks → blacklist (patterns) → aiban_samples (vecteur) → antiflood`.
Sur **join** : `reputation_cache` (CAS : `api.cas.chat/check?user_id=`, `ok:true`=banni ;
Spamwatch DNS mort en 2026 ; custom via config). ⚠️ CAS commercial = afficher
"Powered by CAS".

### II.4 Onboarding & admins
- Hiérarchie : `superadmin` (gère admins) → `admin` (vend/grant/addgroup/addowner) →
  `group_owner` (config son groupe) → `moderator`. + couche native (statut admin Telegram
  via `getChatAdministrators`).
- Tout en base ; seul le tgid de bootstrap superadmin est un param flow (insertion initiale).
- `platform_settings.onboarding_message` éditable à chaud (`/setonboarding`), placeholders
  `{support}`/`{bot}`. Bot ajouté sans entitlement → poste onboarding + `leaveChat` (anti-squat).
- Cycle groupe : `provisioned → active → suspended/expired → removed`.

### II.5 Catalogue de commandes (handlers dans les sous-scripts)
- **Plateforme** : `/addadmin /deladmin /admins /grant /addgroup /addowner /suspend`
  `/setonboarding /broadcast /aiban add|del|list (global)`.
- **Owner** : `/settings /setwelcome /setrules /lock /unlock /blacklist /antiflood`
  `/captcha /aiban (group) /warnlimit`.
- **Modération** : `/ban /unban /kick /mute /unmute /tmute /tban /warn /unwarn /del`
  `/purge /pin /report /rules /info`.
- Captcha configurable : `group_config.captcha_mode ∈ {button, math}`.

---

## Partie III — Schéma SQL (PostgreSQL + pgvector)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Tenancy ──────────────────────────────────────────────────────
CREATE TABLE platform_admins (
  user_id  BIGINT PRIMARY KEY,                 -- tgid
  role     TEXT NOT NULL DEFAULT 'admin',      -- superadmin|admin
  added_by BIGINT,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE platform_settings (
  key TEXT PRIMARY KEY, value TEXT,
  updated_by BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE entitlements (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,                      -- client acheteur (tgid)
  max_groups INT NOT NULL DEFAULT 1,
  plan TEXT NOT NULL DEFAULT 'standard',
  granted_by BIGINT NOT NULL,
  expires_at TIMESTAMPTZ,                        -- NULL = perpétuel
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_entitlements_user ON entitlements(user_id);
CREATE TABLE groups (
  chat_id BIGINT PRIMARY KEY,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'provisioned',   -- provisioned|active|suspended|expired|removed
  bot_status TEXT NOT NULL DEFAULT 'absent',    -- absent|member|administrator
  plan TEXT NOT NULL DEFAULT 'standard',
  expires_at TIMESTAMPTZ, activated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_groups_status  ON groups(status);
CREATE INDEX ix_groups_expires ON groups(expires_at);
CREATE TABLE group_owners (
  chat_id BIGINT NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner',            -- owner|moderator
  added_by BIGINT NOT NULL, added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX ix_group_owners_user ON group_owners(user_id);
CREATE TABLE group_config (
  chat_id BIGINT PRIMARY KEY REFERENCES groups(chat_id) ON DELETE CASCADE,
  language TEXT NOT NULL DEFAULT 'fr',
  welcome_text TEXT, rules_text TEXT,
  warn_limit INT NOT NULL DEFAULT 3,
  warn_action TEXT NOT NULL DEFAULT 'mute',     -- mute|ban|kick
  antiflood_msgs INT NOT NULL DEFAULT 0, antiflood_secs INT NOT NULL DEFAULT 5,
  captcha_enabled BOOLEAN NOT NULL DEFAULT false,
  captcha_mode TEXT NOT NULL DEFAULT 'button',  -- button|math
  captcha_timeout INT NOT NULL DEFAULT 120,
  aiban_enabled BOOLEAN NOT NULL DEFAULT false,
  aiban_threshold REAL NOT NULL DEFAULT 0.85,   -- similarité cosine min
  aiban_action TEXT NOT NULL DEFAULT 'mute',    -- ban|mute
  aiban_gate TEXT NOT NULL DEFAULT 'risky',     -- risky|all
  log_channel_id BIGINT,
  report_enabled BOOLEAN NOT NULL DEFAULT true,
  clean_service BOOLEAN NOT NULL DEFAULT false,
  silent_actions BOOLEAN NOT NULL DEFAULT false
);

-- ── Modération ───────────────────────────────────────────────────
CREATE TABLE warns (
  chat_id BIGINT NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL, count INT NOT NULL DEFAULT 0,
  reasons JSONB NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE temp_restrictions (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  kind TEXT NOT NULL,                            -- mute|ban|captcha
  expires_at TIMESTAMPTZ NOT NULL,
  created_by BIGINT,                             -- NULL = auto
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (chat_id, user_id, kind)
);
CREATE INDEX ix_temprestr_due ON temp_restrictions(expires_at);
CREATE TABLE mod_actions (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL, target_id BIGINT, actor_id BIGINT,
  action TEXT NOT NULL, reason TEXT,
  source TEXT NOT NULL DEFAULT 'manual',         -- manual|antiflood|blacklist|aiban|reputation
  meta JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_modactions_chat ON mod_actions(chat_id, created_at DESC);
CREATE TABLE locks (
  chat_id BIGINT NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
  lock_type TEXT NOT NULL, PRIMARY KEY (chat_id, lock_type)
);
CREATE TABLE blacklist (
  chat_id BIGINT NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
  pattern TEXT NOT NULL,
  action TEXT NOT NULL DEFAULT 'delete',         -- delete|warn|mute|ban
  PRIMARY KEY (chat_id, pattern)
);

-- ── aiban vectoriel ──────────────────────────────────────────────
CREATE TABLE aiban_samples (
  id BIGSERIAL PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'global',          -- global|group
  chat_id BIGINT,                                -- NULL si global
  category TEXT NOT NULL,                         -- décidée par mod_llm
  sample_text TEXT NOT NULL,                      -- audit
  embedding vector(1536) NOT NULL,               -- dim = modèle d'embedding
  embed_model TEXT NOT NULL,                      -- pin du modèle
  added_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_aiban_vec ON aiban_samples USING hnsw (embedding vector_cosine_ops);
-- runtime: SELECT category, 1-(embedding <=> :q) AS sim FROM aiban_samples
--   WHERE scope='global' OR (scope='group' AND chat_id=:c)
--   ORDER BY embedding <=> :q LIMIT 1;

-- ── Réputation externe (CAS, etc.) ───────────────────────────────
CREATE TABLE reputation_cache (
  source TEXT NOT NULL,                           -- cas|spamwatch|custom:<name>
  user_id BIGINT NOT NULL, banned BOOLEAN NOT NULL, reason TEXT,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, user_id)
);

-- ── v2 (plus tard) : notes, filters, federations* ────────────────
```

---

## Partie IV — Prérequis déploiement & GOTCHAS

1. **🔴 Privacy mode OFF** via BotFather (`/setprivacy → Disable`) — sinon le bot ne voit
   que commandes/replies, **pas tous les messages** → aiban/auto-modération aveugles.
2. **Bot admin** avec `can_restrict_members` + `can_delete_messages` ; `chat_member`
   n'arrive que si le bot est admin.
3. **psycopg2** + **pgvector** à installer dans l'image relay
   (`pip install psycopg2-binary`, extension Postgres `vector`).
4. **Réseau host** : Postgres joignable du conteneur via `host.docker.internal`, pas `localhost`.
5. **CAS commercial** : afficher "Powered by CAS" (description bot).
6. **`BIGINT` partout** (chat_id 64-bit — la raison de l'abandon de l'ancien tgbot).
7. **Modèle d'embedding épinglé** : changer de modèle invalide les vecteurs stockés
   (`embed_model` → re-embed). Dim colonne = dim modèle (1536 pour text-embedding-3-small).
8. **Migration supergroupe** : `chat_id` change (`migrate_to_chat_id`) → UPDATE cascade,
   sinon le tenant "disparaît".

---

## Référence

Taxonomie issue de **Marie** (`github.com/PaulSonOfLars/tgbot`, prédécesseur de Rose,
mêmes fonctions) : admin, bans, muting, warns, welcome (+captcha), locks, cust_filters,
blacklist, antiflood, notes, rules, global_bans (federations), reporting, log_channel,
msg_deleting, disable, userinfo/users. v1 vendable = onboarding + ban/kick/mute(+temp) +
warns + welcome/captcha + locks + blacklist + antiflood + rules + log + report + **aiban**
(différenciateur). v2 = notes, filters, federations, connections, approval.
