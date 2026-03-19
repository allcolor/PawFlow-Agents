# PawFlow - Roadmap / Reste à faire

## État actuel
- 73 tasks, 10 services, plugin system (versioning semver, upgrade/downgrade)
- 3 modes d'exécution (batch, continu, CRON) + **debugger graphique** (breakpoints, step, inspection)
- GUI Streamlit (5 pages), CLI (11 commandes), **API REST FastAPI (10 routeurs, 100+ endpoints)**
- **935 tests verts**
- Sécurité RBAC, OAuth2, API keys, sessions, PBKDF2, secrets chiffrés, sandbox
- Workers distants, checkpointing, streaming/spill, **cluster mode**
- Notifications (webhook, handlers, event filtering)
- GUI: mode direct + mode API REST, **data preview**, **flow diff**
- Docker + docker-compose (API + GUI + PostgreSQL)
- **15 flow templates** (ETL, Monitoring, Communication, Data Processing, Integration)
- **Event triggers** : file watcher, webhook, event-driven, polling
- **i18n** : GUI trilingue (EN/FR/ES), extensible

---

## P1 — API REST ✅ DONE

### 1.1 API REST complète avec FastAPI
- [x] FastAPI + uvicorn + python-multipart
- [x] Structure `api/` avec 9 routeurs modulaires
- [x] Auth middleware (Bearer session token / API key / disabled mode)
- [x] 50+ endpoints couvrant tous les domaines
- [x] Documentation OpenAPI auto-générée (/docs)
- [x] 39 tests API (tous verts)
- [x] WebSocket pour streaming temps réel (logs, métriques, queue stats)

---

## P2 — Mise à jour documentation ✅ DONE

- [x] `docs/README.md` : mis à jour (491 tests, 49 tasks, API REST)
- [x] `docs/architecture.md` : SpillTracker, Connection, ContinuousFlowExecutor, etc.
- [x] `docs/development.md` : phases 1-10 DONE, guides mis à jour
- [x] `docs/02_REFERENCE_TASKS_SERVICES.md` :
  - Services corrigés (5 réels au lieu de 15 fantômes)
  - Section 13 (Execution) : FlowExecutor, ContinuousFlowExecutor, Scheduler, API REST
  - Section 15 (Sécurité) : RBAC réel avec rôles/permissions
  - Section 16 (Tests) : 491 tests, fichiers listés

---

## P3 — Backends de stockage ✅ DONE

- [x] Git Storage (subprocess git, auto-commit, history, time-travel)
- [x] PostgreSQL Storage (psycopg2, JSONB, UPSERT)
- [x] 30 tests (Git, SQLite, Filesystem, StorageManager)

---

## P4 — TODOs GUI ✅ DONE (all)

- [x] Téléchargement de résultats d'exécution (st.download_button JSON)
- [x] Suppression d'exécutions (delete_execution + rerun)
- [x] Graphiques time-series pour métriques (st.line_chart durée + succès/échecs)
- [x] Upload fichier d'entrée (st.file_uploader remplace le file browser)
- [x] Connecter la GUI à l'API REST (mode dual : direct + API HTTP via `PawFlowApiClient`)

---

## P5 — Tasks manquantes NiFi ✅ DONE (core)

### Communication ✅
- [x] SendEmail (SMTP, TLS, pièces jointes)
- [x] NotifySlack (Incoming Webhook)

### Formats ✅
- [x] ParseXML (XML → JSON)
- [x] TransformXML (JSON → XML)

### Transfert ✅
- [x] GetSFTP / PutSFTP (via paramiko)
- [x] GetFTP / PutFTP (ftplib, FTPS)

### Messaging ✅
- [x] PublishKafka / ConsumeKafka (via kafka-python)

### Cloud ✅
- [x] PutS3 / GetS3 (via boto3, compatible MinIO)

### MQTT ✅
- [x] PublishMQTT / ConsumeMQTT (via paho-mqtt)

### Formats colonnaires ✅
- [x] ConvertAvroToJSON / ConvertJSONToAvro (via fastavro)
- [x] ConvertParquetToJSON / ConvertJSONToParquet (via pyarrow)

### Cloud ✅
- [x] PutGCS / GetGCS (via google-cloud-storage)
- [x] PutAzureBlob / GetAzureBlob (via azure-storage-blob)

---

## P6 — Export/Import .pfp ✅ DONE (core)

- [x] Exporter un flow en .pfp (avec ses tasks custom et services) — `export_flow_as_plugin()`
- [x] Importer un .pfp contenant tasks+services+flows — `PluginManager.install()` (existait déjà)
- [x] API REST endpoint POST /plugins/export (retourne .pfp en téléchargement)
- [x] 4 tests export (basic, custom id, strip internal, roundtrip)
- [ ] Marketplace / registre de plugins (browse, search, install depuis URL)
- [ ] Versioning de plugins (upgrade, downgrade)

---

## P7 — Améliorations infra ✅ DONE (core)

- [x] WebSocket streaming temps réel (`/ws/bulletins`, `/ws/execution/{id}`, `/ws/metrics`)
- [x] Audit log complet (qui, quoi, quand) — `core/audit.py` + API endpoints
- [x] Rate limiting API — `api/rate_limit.py` (env var PAWFLOW_RATE_LIMIT=true)
- [x] 17 tests (audit, rate limiter, websocket)

### Notifications ✅
- [x] NotificationManager (singleton, thread-safe)
- [x] Webhook HTTP (POST JSON, filtrage par event type, wildcards)
- [x] Python handlers (callables, event filtering)
- [x] Historique, statistiques
- [x] API endpoints (5 endpoints: register/list/delete webhook, history, stats)
- [x] 18 tests (singleton, webhook CRUD, handlers, filtering, thread safety)

### Métriques ✅
- [x] **Endpoint Prometheus** : `GET /api/v1/system/metrics` — format text/plain compatible Prometheus
  - pawflow_info, uptime, tasks_registered, services_registered, audit_events, notifications, sessions, users

### Reste (optionnel)
- [x] Cluster mode (coordination multi-instance) — `engine/cluster.py`, filesystem-backed state, auto-promotion, heartbeat, election
- [ ] OpenTelemetry tracing (spans pour chaque task execution)
- [ ] Flow templates / marketplace

---

## P8 — Parameter Context & Subflow Mapping 🔴 TODO

> **Problème** : `Flow.parameters` est parsé et stocké mais **jamais injecté** dans les tâches.
> La chaîne param → tâche est brisée à 5 endroits. Les subflows ne reçoivent pas les params du parent.

### 8.1 Parameter Context — injection des params dans les tâches ✅ DONE
- [x] **ParameterContext** (`core/parameter_context.py`) : objet immutable, merge, mapping, resolve_config
- [x] **FlowExecutor** : crée le ParameterContext depuis `flow.parameters` + overrides, injecte dans toutes les tâches
- [x] **ContinuousFlowExecutor** : idem, accepte `parameters=` override au constructeur
- [x] **BaseTask** : `set_parameter_context(ctx)` re-résout le config original, `resolve_value()` pour runtime
- [x] **resolve_expression()** appelé avec `parameters=` via ParameterContext.resolve()
- [x] **ExecuteFlowTask** : propage le ParameterContext au subflow (directement ou via mapping)
- [x] 36 tests : unit ParameterContext, BaseTask injection, FlowExecutor e2e, ContinuousExecutor, subflow propagation

### 8.2 Subflow parameter mapping ✅ DONE
- [x] **Modèle de mapping** : `ExecuteFlowTask.config.parameter_mapping = {"subflow_param": "${flow.parameters.parent_param}"}`
- [x] **ExecuteFlowTask** : injecte le mapping dans `subflow.parameters` avant exécution (fait en P8.1)
- [x] **Propagation chaînée** : parent → child → grandchild, params hérités via ParameterContext
- [x] **Validation** : warning log pour params subflow non-résolus (`_validate_subflow_params`)
- [x] Tests : mapping, chaîné, littéraux, defaults subflow, warning params manquants

### 8.3 Déploiement / instantiation avec override de params ✅ DONE
- [x] **API batch** : `POST /execution/batch` accepte `parameters` en body
- [x] **API continuous** : `POST /execution/continuous/start` accepte `parameters` en body
- [x] **CLI** : `python cli.py run flow.json --param env=prod --param port=9000`
- [x] Tests : modèles API, parsing CLI
- [x] **GUI Runtime** : formulaire de saisie des flow parameters avant lancement (batch + continu)
- [x] **ExecutionService** : accepte `parameters=` pour override
- [x] **Scheduler** : les jobs CRON peuvent spécifier des param overrides par schedule

### 8.4 GUI Editor — mapping visuel subflow params ✅ DONE (all)
- [x] **Flow Parameters** dans la sidebar éditeur : ajouter/éditer/supprimer les paramètres du flow (avec référence `${flow.parameters.X}`)
- [x] **Panel subflow** dans l'éditeur : quand on configure un ExecuteFlowTask avec un flow_path valide, affiche les params du subflow
- [x] **UI mapping** : pour chaque param du subflow, choisir la source (défaut subflow, param du parent, expression personnalisée)
- [x] **Validation visuelle** : indicateur ✅/⚪ par param mappé/non-mappé
- [x] **Relations in/out** : lier les InputPort/OutputPort du subflow aux connexions du flux parent (port_mapping config, GUI section "Subflow Ports")

### Ordre de priorité
1. **8.1** (ParameterContext) — sans ça, rien ne marche ✅
2. **8.2** (Subflow mapping) — essentiel dès qu'on compose des flows
3. **8.3** (Override au déploiement) — nécessaire pour rendre les flows réutilisables
4. **8.4** (GUI mapping) — confort, peut venir après

---

## P9 — Import NiFi → PawFlow ✅ DONE (core)

> **Objectif** : Pouvoir importer un flow NiFi (XML/JSON export) et le convertir en flow PawFlow,
> incluant la conversion/adaptation des scripts Groovy en Python.
> Le tout faisable depuis la GUI.

### 9.1 Parseur de flow NiFi ✅ DONE
- [x] **Parser NiFi XML** : templates XML et processGroup (`engine/nifi_converter.py`)
- [x] **Parser NiFi JSON** : format REST API NiFi (processGroupFlow, processors, connections)
- [x] **Auto-detection format** : XML ou JSON automatique
- [x] **Mapping processeurs NiFi → tasks PawFlow** : 50+ processeurs mappés (IO, Data, Control, Script, Kafka, S3, MQTT, SFTP, etc.)
- [x] **Mapping controller services NiFi → services PawFlow** : DBCPConnectionPool→dbConnectionPool, etc.
- [x] **Extraction des relations** : connexions NiFi (relationships success/failure/etc.) → relations PawFlow
- [x] **Extraction des paramètres** : parameter contexts NiFi → flow.parameters PawFlow
- [x] **Input/Output ports** : conversion vers inputPort/outputPort PawFlow, entries/exits
- [x] **Processeurs non-mappés** : identifiés et remplacés par log task avec warning
- [x] **Extraction des scripts** : Groovy scripts extraits pour conversion séparée
- [x] 36 tests (XML, JSON, auto-detect, mapping, scripts, ports, params, e2e)

### 9.2 Conversion de scripts Groovy → Python ⭐ IMPORTANT

> **Architecture** : La conversion est une **feature interne PawFlow** (pas une task/flux).
> Elle a sa propre config LLM (api_key, base_url, model) dans `config/pawflow.json` (ou Settings GUI).
> Le code de communication LLM est **partagé** avec `services/llm_connection.py` (module commun).
> C'est du **one-shot** : la conversion a lieu une seule fois lors de l'import du flux NiFi.

- [x] **Module LLM partagé** : `core/llm_client.py` (LLMClient standalone, zero deps) — réutilisé par LLMConnectionService et NiFi converter
- [x] **NiFiScriptConverter** : `engine/nifi_script_converter.py` — conversion Groovy→Python
- [x] **Conversion via LLM** : appeler le LLM configuré avec un prompt spécialisé
  - Prompt système taillé pour la conversion NiFi Groovy → PawFlow Python
  - Inclure dans le prompt : la table de mapping API NiFi → API PawFlow
    - `session.get()` → `flowfile` (paramètre de execute)
    - `session.read(flowfile)` → `flowfile.get_content()`
    - `session.write(flowfile)` → `flowfile.set_content()`
    - `session.transfer(flowfile, REL_SUCCESS)` → `return [flowfile]`
    - `flowfile.getAttribute()` → `flowfile.get_attribute()`
    - `flowfile.putAttribute()` → `flowfile.set_attribute()`
    - Types Java (String, ArrayList, HashMap) → types Python (str, list, dict)
    - Imports Java courants → équivalents Python (JsonSlurper→json.loads, etc.)
  - Le LLM reçoit le script Groovy complet + la doc API PawFlow + exemples de conversion
  - Réponse structurée : code Python + liste de warnings/points à vérifier manuellement
- [x] **Fallback règles statiques** : conversion regex sans LLM (session API, types Java, imports, JSON, logging)
- [x] **Mode semi-auto** : le LLM marque les zones incertaines avec `# TODO: manual review`
- [x] **Aller/retour LLM** : `convert_with_feedback()` pour re-soumettre avec corrections utilisateur
- [x] **Config LLM conversion** : section dans Settings GUI (api_key, base_url, model pour la conversion)
- [ ] **Script wrapper** : pour les cas trop complexes, wrapper subprocess JVM en fallback
- [x] Tests : 36 tests (static conversion, LLM mock, client partagé, e2e)

### 9.3 GUI — import NiFi ✅ DONE
- [x] **Onglet "Import NiFi"** dans Settings : upload fichier NiFi (XML/JSON)
- [x] **Preview** : métriques (tasks, relations, params), warnings, processeurs non-mappés, JSON preview
- [x] **Config LLM** : section configurable (provider, api_key, base_url, model)
- [x] **Édition des scripts** : Groovy original et Python généré côte à côte, éditable
- [x] **Aller/retour LLM** : champ feedback pour re-soumettre au LLM avec indications
- [x] **Boutons Finaliser** : "Importer dans PawFlow" (sauve en flows/) ou "Ouvrir dans l'éditeur"

### 9.4 Mapping étendu & process groups ✅ DONE (core)
- [x] 50+ processeurs NiFi mappés (IO, Data, Control, Script, Kafka, S3, MQTT, SFTP, XML, Avro, Parquet)
- [x] **Process groups NiFi → subflows PawFlow** : chaque process group imbriqué génère un subflow séparé + executeFlow dans le parent
- [x] Récursif : process groups imbriqués N niveaux
- [x] Connexions parent ↔ process group mappées via id_map
- [x] GUI: subflows sauvés automatiquement lors de l'import, métrique affichée
- [x] 9 tests process group (XML + JSON, subflow content, connections, isolation)
- [ ] Documenter les processeurs NiFi non supportés avec alternatives suggérées

### Notes techniques
- NiFi export XML : `<template>` ou `<processGroup>` avec `<processors>`, `<connections>`, `<controllerServices>`
- NiFi REST JSON : `/flow/process-groups/{id}` retourne `processGroupFlow` avec `processors[]`, `connections[]`
- Les scripts Groovy NiFi utilisent l'API `ProcessSession` — mapping bien documenté vers l'API FlowFile PawFlow
- Certains processeurs NiFi (UpdateAttribute, RouteOnAttribute) ont déjà un mapping 1:1 avec PawFlow

---

## P10 — Sécurité production ✅ DONE

- [x] **PBKDF2 password hashing** : 600K itérations, salt 32 bytes, auto-upgrade des hash legacy
- [x] **CORS restrictif** : configurable via `PAWFLOW_CORS_ORIGINS` (défaut localhost seulement)
- [x] **Secrets chiffrés** : `core/secrets.py` (XOR+PBKDF2+HMAC, clé via env var ou fichier)
- [x] **Sandbox executeScript** : imports restreints, builtins filtrés, `allowed_modules` configurable
- [x] **Validation requêtes** : middleware taille max body (10MB, configurable)
- [x] 28 tests (hashing, CORS, encryption, sandbox, validation)

---

## P11 — Docker & Documentation ✅ DONE

- [x] **Dockerfile** : Python 3.12-slim, requirements.txt
- [x] **docker-compose.yml** : API + GUI + PostgreSQL (profil optionnel)
- [x] **.dockerignore** + **.env.example**
- [x] **requirements.txt** : dépendances core + optionnelles commentées
- [x] **docs/deployment.md** : guide déploiement (local, Docker, production checklist)
- [x] **Mise à jour docs** : README, architecture, development, reference (757 tests, 68 tasks, 85+ endpoints)

---

## P12 — CLI étendu ✅ DONE

- [x] `cli.py serve` : lancer l'API server (--host, --port, --reload)
- [x] `cli.py gui` : lancer la GUI Streamlit (--host, --port, --headless)
- [x] `cli.py plugins list|install|remove` : gestion des plugins
- [x] `cli.py scheduler list|add|remove|start` : gestion CRON
- [x] `cli.py export <flow> [-o output]` : export en .pfp
- [x] `cli.py import <file> [-o output]` : import NiFi ou .pfp
- [x] `cli.py cluster status [--api-url]` : statut cluster
- [x] 20 tests CLI

---

## P13 — Flow Templates ✅ DONE

- [x] 15 templates builtin (ETL, Monitoring, Communication, Data Processing, Integration)
- [x] Metadata : catégorie, tags, difficulté, services requis, auteur
- [x] Search/filter par nom, tags, catégorie
- [x] Import/export templates (JSON)
- [x] API endpoints (list, get, save) — `GET/POST /api/v1/flows/templates`
- [x] GUI : templates groupés par catégorie, preview, badge difficulté
- [x] 14 tests templates

---

## P14 — Plugin Versioning ✅ DONE

- [x] `PluginVersion` : parsing semver, comparaison, compatibilité, constraints (`>=1.0.0`)
- [x] `PluginManager.upgrade()` / `downgrade()` : backup, install, rollback si échec
- [x] Historique versions (`plugins/versions/{id}/history.json`)
- [x] Dépendances entre plugins (`dependencies: {"other-plugin": ">=1.0.0"}`)
- [x] API endpoints : versions, upgrade, downgrade, history
- [x] CLI : `plugins upgrade/downgrade/history/info`
- [x] 39 tests versioning

---

## P15 — Debugger Graphique ✅ DONE

- [x] `FlowDebugger` (`engine/debugger.py`) : breakpoints, step, continue, stop
- [x] Breakpoints conditionnels (expression sur attributes), logpoints
- [x] Snapshots FlowFile (content preview 1000 chars, attributes, direction)
- [x] Intégré dans ContinuousFlowExecutor (hooks before/after execution)
- [x] 7 API endpoints debug (breakpoint CRUD, continue, step, stop, snapshots)
- [x] GUI : panel debug dans Runtime (status, contrôles, inspector, snapshot history)
- [x] 31 tests debugger

---

## P16 — Data Preview & Flow Diff ✅ DONE

- [x] `DataPreviewManager` (`engine/data_preview.py`) : capture FlowFiles aux connexions
- [x] Détection type contenu (json, xml, csv, text, binary)
- [x] Preview par connexion ou global, max samples, thread-safe
- [x] Intégré dans ContinuousFlowExecutor (capture au commit)
- [x] `FlowDiff` (`engine/flow_diff.py`) : comparaison structurée (tasks, relations, params, metadata)
- [x] API endpoints : preview enable/disable/samples/connections + diff flows
- [x] 39 tests (15 preview + 8 content detection + 16 diff)

---

## P17 — Event Triggers ✅ DONE

- [x] `TriggerManager` (`engine/triggers.py`) : création, lifecycle, persistance, historique
- [x] **FileWatcherTrigger** : surveille un dossier (patterns, on_create/on_modify, move_after)
- [x] **WebhookTrigger** : serveur HTTP (port, path, HMAC secret validation)
- [x] **EventTrigger** : réagit aux événements internes via NotificationManager (filter payload)
- [x] **PollingTrigger** : vérifie URL périodiquement (status_ok, content_changed, json_match)
- [x] API router `/api/v1/triggers` : CRUD, start/stop/pause/resume, history
- [x] GUI : onglet Triggers dans Runtime (formulaire par type, contrôles, historique)
- [x] CLI : `triggers list/create/start/stop/delete/history`
- [x] 38 tests triggers

---

## P18 — i18n / Localisation GUI ✅ DONE

- [x] Module `gui/i18n/` : JSON-based, zero deps, interpolation `{variable}`, fallback anglais
- [x] 3 langues : English (en), Français (fr), Español (es) — 121 clés chacune
- [x] Sélecteur de langue dans la sidebar GUI
- [x] `t("key")` dans les pages (main.py intégré, autres pages prêtes à migrer)
- [x] Ajout d'une langue = 1 fichier JSON + 1 ligne dans `SUPPORTED_LOCALES`
- [x] 17 tests (traductions, interpolation, fallback, parité clés entre langues)

---

## P19 — HTTP Listener Service ✅ DONE

### 19.1 Shared HTTP Listener
- [x] `services/http_listener_service.py` : HTTPListenerService — singleton par port, ThreadingHTTPServer
- [x] Route Registry : method + URL pattern matching avec `{path_params}`
- [x] PendingRequest : corrélation request/response via threading.Event
- [x] 404 si aucun route ne matche, 504 si timeout, 503 à l'arrêt
- [x] Route conflict detection entre flows (même route = erreur)
- [x] Ref-counting : plusieurs flows partagent le même port
- [x] Support SSL/TLS : config directe (certfile/keyfile) ou via SSLContextService
- [x] `services/ssl_context_service.py` : SSLContextService — fournit ssl.SSLContext partageable

### 19.2 HTTP Tasks
- [x] `tasks/io/http_receiver.py` : httpReceiver — source self-triggering avec has_pending_input()
- [x] `tasks/io/handle_http_response.py` : handleHTTPResponse — renvoie la réponse HTTP
  - Status code configurable (config + override via attribut `http.response.status`)
  - Headers personnalisables (config + override via `http.response.header.*`)
  - Body = contenu FlowFile ou override via `http.response.body`
- [x] `tasks/io/validate_http_auth.py` : validateHTTPAuth — validation Bearer/Basic auth
  - Auto-réponse 401/403 si auth invalide
  - Routing vers "failure" relationship
- [x] `services/http_auth_service.py` : HTTPAuthService — validation Bearer tokens, Basic auth, custom

### 19.3 Engine Support
- [x] `core/base_task.py` : ajout `has_pending_input() -> bool` (protocol self-triggering)
- [x] `engine/continuous_executor.py` :
  - Scheduler loop gère les self-triggering tasks (root sans incoming)
  - _execute_task gère l'exécution sans source_conn
  - stop() appelle cleanup() sur les tasks + disconnect() sur les services

### 19.4 GUI
- [x] Tasks httpReceiver, handleHTTPResponse, validateHTTPAuth dans la palette IO
- [x] Section Services dans l'éditeur (ajout/suppression/config JSON)
- [x] Services httpListener, httpAuthValidator, sslContext auto-découverts

### 19.5 Demo & Docs
- [x] Flow hello world : `flows/http_hello_world.json` — GET /api/helloworld/{who} → <h1>Hello {who}</h1>
- [x] Documentation : `docs/http_listener.md` — architecture, composants, exemples, intégration GUI
- [x] 43 tests (route registry, pending requests, service lifecycle, auth, integration full-cycle)

---

## P20 — Enhanced File Listing & Tracking ✅ DONE

- [x] `services/file_tracking_service.py` : FileTrackingService
  - Strategies: lastModified, md5, both
  - Persistent tracking (JSON), auto-pruning, reset per-file or global
- [x] `tasks/system/list_files.py` v2.0 : ListFilesTask enhanced
  - Filters: glob pattern, regex, file_extensions, min/max_size, min/max_age
  - FileTrackingService integration (skip already-processed files)
  - Self-triggering mode (polling_interval for continuous execution)
  - FlowFile attributes: filename, path, absolute.path, fileSize, file.lastModified, file.extension
- [x] `tasks/io/list_sftp.py` : ListSFTPTask
  - Same filtering + tracking as listFiles, for SFTP directories
  - Uses paramiko, recursive listing, self-triggering
- [x] GUI: listSFTP added to IO palette
- [x] 32 tests (FileTrackingService, ListFilesTask filters/tracking/polling, ListSFTPTask filters)

---

## P21 — Crash Recovery & Flow Versioning ✅ DONE

- [x] `engine/flow_state.py` : FlowStateManager + FlowVersionStore
  - FlowStateManager: persists running flows to `config/running_flows.json`
  - FlowVersionStore: saves flow config versions for rollback (`config/flow_versions/`)
- [x] Crash recovery in `api/app.py` lifespan:
  - On startup: detects flows that were running before crash
  - Auto-restarts with checkpoint recovery (queued FlowFiles preserved)
  - Marks failed recoveries with error details
- [x] API endpoints:
  - `GET /recovery/status` — view crashed/failed/recovered flows
  - `POST /recovery/{flow_id}/retry` — retry failed recovery
  - `DELETE /recovery/{flow_id}` — dismiss from recovery list
  - `GET /continuous/{flow_id}/config-versions` — list saved config versions
  - `GET /continuous/{flow_id}/config-versions/{version}` — get specific version
  - `POST /continuous/{flow_id}/downgrade/{version}` — rollback to previous version (hot-update if running)
- [x] Graceful shutdown: stops all running executors, saves checkpoints
- [x] Flow downgrade: saves backup before downgrade, writes old config, hot-updates running executor
- [x] 19 tests (FlowStateManager persistence/recovery/lifecycle, FlowVersionStore versioning/pruning)

---

## Reste à faire (optionnel / futur)

- [ ] OpenTelemetry tracing (spans par task execution)
- [ ] Marketplace plugins centralisé (registre URL, browse, search)
- [x] Config LLM conversion dans Settings GUI
- [x] pyproject.toml (modern packaging)
- [x] Non-root Docker user
- [x] Drag-and-drop task placement on canvas (forked streamlit-flow-component, in-canvas palette)
- [x] Flow versioning: save-only-on-diff, version archive (flows/versions/), version selector au Runtime
- [x] Fix: hot-update skip si flow inchangé, restore flow_version au restart, affichage version JSON
- [ ] Doc processeurs NiFi non supportés avec alternatives

## P22 — Agent LLM Flow ✅ DONE

Flux agent conversationnel avec tool-use loop, exposé via HTTP.

Architecture : `httpReceiver → agentLoop → handleHTTPResponse`

### Sous-tâches

- [x] **LLMClient : support tool_use** — `LLMToolDefinition`, `LLMToolCall`, `LLMToolResult` dataclasses, `tools` param dans `complete()`, parsing tool_calls OpenAI + Anthropic, multi-turn messages avec tool results
- [x] **Task `agentLoop`** — task composite `tasks/ai/agent_loop.py` : boucle LLM → tool_call → execute → LLM → ... → réponse finale
  - Config : tools (JSON schema), system_prompt, paramètres LLM, max_iterations
  - Attributs sortie : agent.iterations, agent.tools_called, agent.model, agent.tokens_in/out, agent.duration_ms
- [x] **Tool dispatch system** — `core/tool_registry.py` : ToolHandler interface, ToolRegistry, create_default_registry()
  - Handlers builtin : execute_script (sandbox), fetch_http, read_file
- [x] **Conversation persistence** — historique par `conversation_attribute` (sérialisation JSON dans les attributs FlowFile)
- [x] **Flow template "Agent"** — template `builtin_agent_llm` (httpReceiver → agentLoop → handleHTTPResponse)
- [x] **LLMConnectionService** — forward `tools` parameter
- [x] **i18n** — 8 clés agent.* en EN/FR/ES
- [x] **54 tests** (dataclasses, message building OpenAI/Anthropic, response parsing, tool registry, builtin handlers, agent loop logic, max iterations, conversation persistence/restore, template, i18n)
