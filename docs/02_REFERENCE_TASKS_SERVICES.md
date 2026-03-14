# Documentation Technique - Suite (Sections 11-17)

## 11. Référence Complète des Tâches

### 11.1. Tâches de Base (System)

#### 11.1.1. Log Task (`log`)
**Description** : Logguer un message avec formatage

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `message` | string | Oui | - | Message à logguer (supporte les variables) |
| `level` | select | Non | INFO | Niveau de log (DEBUG, INFO, WARNING, ERROR) |
| `logger_name` | string | Non | - | Nom du logger (défaut : nom de la tâche) |
| `include_attributes` | boolean | Non | false | Inclure les attributs du FlowFile dans le log |

**Exemple** :
```json
{
  "type": "log",
  "parameters": {
    "message": "Traitement de ${filename}, taille: ${fileSize}",
    "level": "INFO",
    "include_attributes": true
  }
}
```

#### 11.1.2. Replace Text Task (`replace_text`)
**Description** : Remplacer du texte dans le contenu du FlowFile

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `search_pattern` | string | Oui | - | Motif de recherche (regex ou texte) |
| `replacement` | string | Oui | - | Texte de remplacement |
| `regex` | boolean | Non | false | Utiliser regex (true) ou texte simple (false) |
| `case_sensitive` | boolean | Non | true | Sensible à la casse |
| `multiline` | boolean | Non | false | Multi-lignes |

**Exemple** :
```json
{
  "type": "replace_text",
  "parameters": {
    "search_pattern": "\\bold\\b",
    "replacement": "new",
    "regex": true,
    "case_sensitive": false
  }
}
```

#### 11.1.3. Wait Task (`wait`)
**Description** : Attendre une durée avant de continuer

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `duration` | integer | Oui | - | Durée en millisecondes |
| `duration_unit` | select | Non | MS | Unité (MS, SEC, MIN, HOUR) |

**Exemple** :
```json
{
  "type": "wait",
  "parameters": {
    "duration": 1000,
    "duration_unit": "MS"
  }
}
```

#### 11.1.4. Notify Task (`notify`)
**Description** : Envoyer une notification (email, webhook, etc.)

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `notification_type` | select | Oui | - | Type (email, webhook, slack) |
| `service_ref` | reference | Oui | - | Référence au service de notification |
| `subject` | string | Non | - | Sujet (pour email) |
| `body` | string | Non | - | Corps du message |
| `recipients` | array | Non | [] | Liste de destinataires |
| `on_success` | boolean | Non | true | Envoyer uniquement en cas de succès |
| `on_failure` | boolean | Non | true | Envoyer uniquement en cas d'échec |

**Exemple** :
```json
{
  "type": "notify",
  "parameters": {
    "notification_type": "email",
    "service_ref": "${email_service}",
    "subject": "Pipeline terminé",
    "body": "Le flux ${flow_name} a terminé avec succès.",
    "recipients": ["admin@example.com"]
  }
}
```

#### 11.1.5. Route Task (`route`)
**Description** : Router le FlowFile vers différentes sorties selon des critères

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `route_definitions` | json | Oui | - | Définition des routes |
| `default_route` | string | Non | "unmatched" | Route par défaut |

**Schema route_definitions** :
```json
{
  "route_1": "${attribute} == 'value1'",
  "route_2": "${attribute} == 'value2'",
  "default": "unmatched"
}
```

#### 11.1.6. Split Task (`split`)
**Description** : Splitter un FlowFile en plusieurs FlowFiles

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `split_strategy` | select | Oui | - | Stratégie (line, record, size) |
| `split_count` | integer | Non | - | Nombre de splits (pour size) |

**Exemple line** :
```json
{
  "type": "split",
  "parameters": {
    "split_strategy": "line"
  }
}
```

#### 11.1.7. Merge Task (`merge`)
**Description** : Fusionner plusieurs FlowFiles en un seul

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `merge_strategy` | select | Oui | - | Stratégie (time, count, batch) |
| `merge_timeout` | integer | Non | 30 | Timeout en secondes |
| `merge_count` | integer | Non | 10 | Nombre de FlowFiles à fusionner |

### 11.2. Tâches de Traitement de Données

#### 11.2.1. Script Task (`script`)
**Description** : Exécuter un script Python personnalisé

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `script` | textarea | Oui | - | Code Python du script |
| `script_type` | select | Non | inline | Type (inline, file) |
| `input_var_name` | string | Non | flowfile | Nom de la variable d'entrée |
| `output_var_name` | string | Non | result | Nom de la variable de sortie |
| `variables` | json | Non | {} | Variables supplémentaires |

**Script Template** :
```python
def process(input_var_name):
    # input_var_name est un FlowFile
    # return un FlowFile ou une liste de FlowFiles
    return input_var_name
```

#### 11.2.2. Shell Task (`shell`)
**Description** : Exécuter une commande shell

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `command` | string | Oui | - | Commande à exécuter |
| `working_directory` | string | Non | - | Répertoire de travail |
| `environment` | json | Non | {} | Variables d'environnement |
| `timeout` | integer | Non | 300 | Timeout en secondes |
| `capture_output` | boolean | Non | true | Capturer stdout/stderr |

#### 11.2.3. Convert Task (`convert`)
**Description** : Convertir le format de données

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `input_format` | select | Oui | - | Format d'entrée (json, csv, xml, avro, parquet) |
| `output_format` | select | Oui | - | Format de sortie |
| `schema` | json | Non | - | Schéma (pour formats structurés) |
| `options` | json | Non | {} | Options spécifiques au format |

#### 11.2.4. Filter Task (`filter`)
**Description** : Filtrer les FlowFiles selon un critère

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `condition` | string | Oui | - | Condition (expression Python ou JEXL) |
| `match` | select | Non | true | true = garder match, false = exclure match |

**Exemple** :
```json
{
  "type": "filter",
  "parameters": {
    "condition": "${fileSize} > 1000",
    "match": true
  }
}
```

#### 11.2.5. Validate Task (`validate`)
**Description** : Valider un FlowFile selon un schéma

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `schema` | json | Oui | - | Schéma de validation (JSON Schema, Avro, etc.) |
| `schema_format` | select | Non | json | Format du schéma |
| `on_invalid` | select | Non | fail | Action (fail, route, skip) |
| `route_invalid_to` | string | Non | - | Route pour les invalides |

### 11.3. Tâches d'Entrée/Sortie

#### 11.3.1. HTTP Task (`http`)
**Description** : Appeler une API HTTP

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `url` | string | Oui | - | URL de l'endpoint |
| `method` | select | Non | GET | Méthode (GET, POST, PUT, DELETE, PATCH) |
| `headers` | json | Non | {} | En-têtes HTTP |
| `body` | string | Non | - | Corps de la requête |
| `auth_service` | reference | Non | - | Service d'authentification |
| `timeout` | integer | Non | 30 | Timeout en secondes |
| `follow_redirects` | boolean | Non | true | Suivre les redirects |
| `response_handling` | select | Non | content | Action (content, status, both) |

**Exemple** :
```json
{
  "type": "http",
  "parameters": {
    "url": "https://api.example.com/data",
    "method": "POST",
    "headers": {
      "Content-Type": "application/json"
    },
    "body": "${content}",
    "auth_service": "${oauth_service}"
  }
}
```

#### 11.3.2. HTTP Source Task (`http_source`)
**Description** : Source HTTP (polling ou webhook)

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `url` | string | Oui | - | URL à poller |
| `method` | select | Non | GET | Méthode |
| `polling_interval` | integer | Non | 60 | Intervalle en secondes |
| `headers` | json | Non | {} | En-têtes |

#### 11.3.3. SFTP Task (`sftp`)
**Description** : Opérations SFTP

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (get, put, list, delete, rename) |
| `service_ref` | reference | Oui | - | Service SFTP |
| `remote_path` | string | Oui | - | Chemin distant |
| `local_path` | string | Non | - | Chemin local (pour put/get) |
| `filename_pattern` | string | Non | * | Pattern de fichiers |
| `overwrite` | boolean | Non | false | Écraser existant |

#### 11.3.4. S3 Task (`s3`)
**Description** : Opérations AWS S3

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (get, put, delete, list) |
| `service_ref` | reference | Oui | - | Service S3 |
| `bucket` | string | Oui | - | Nom du bucket |
| `key` | string | Non | - | Clé S3 |
| `prefix` | string | Non | - | Préfixe (pour list) |
| `max_keys` | integer | Non | 1000 | Max clés (pour list) |
| `version_id` | string | Non | - | Version (pour get) |

#### 11.3.5. Database Task (`db`)
**Description** : Opérations base de données

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (query, update, insert, delete, bulk) |
| `service_ref` | reference | Oui | - | Service DB |
| `query` | textarea | Oui | - | Requête SQL |
| `parameters` | json | Non | {} | Paramètres de requête |
| `batch_size` | integer | Non | 1000 | Taille batch |

#### 11.3.6. File Task (`file`)
**Description** : Opérations sur fichiers locaux

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (read, write, delete, rename) |
| `path` | string | Oui | - | Chemin du fichier |
| `path_type` | select | Non | absolute | Type (absolute, relative, home) |
| `encoding` | select | Non | utf-8 | Encodage |
| `create_dirs` | boolean | Non | true | Créer les répertoires |

#### 11.3.7. Kafka Task (`kafka`)
**Description** : Publier/consommer Kafka

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (publish, consume) |
| `service_ref` | reference | Oui | - | Service Kafka |
| `topic` | string | Oui | - | Topic |
| `key` | string | Non | - | Clé du message |
| `partition` | integer | Non | - | Partition |
| `headers` | json | Non | {} | En-têtes Kafka |

### 11.4. Tâches de Contrôle

#### 11.4.1. Flow Call Task (`flow_call`)
**Description** : Appeler un autre flux

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `flow_id` | string | Oui | - | ID du flux à appeler |
| `flow_version` | string | Non | latest | Version du flux |
| `input_mode` | select | Non | single | Mode (single, batch) |
| `variables` | json | Non | {} | Variables à passer |
| `wait_for_completion` | boolean | Non | true | Attendre la fin |
| `output_mode` | select | Non | collect | Mode (collect, stream) |

#### 11.4.2. Sleep Task (`sleep`)
**Description** : Mettre en pause l'exécution

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `duration` | integer | Oui | - | Durée en millisecondes |

#### 11.4.3. Fail Task (`fail`)
**Description** : Échouer explicitement le FlowFile

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `message` | string | Non | - | Message d'erreur |
| `terminate` | boolean | Non | true | Terminer le flow entier |

#### 11.4.4. Choose Task (`choose`)
**Description** : Choisir entre plusieurs branches (switch)

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `expression` | string | Oui | - | Expression à évaluer |
| `branches` | json | Oui | - | Branches conditionnelles |

**Schema branches** :
```json
{
  "branch_1": "${expression} == 'value1'",
  "branch_2": "${expression} == 'value2'",
  "default": "branch_default"
}
```

#### 11.4.5. Join Task (`join`)
**Description** : Joindre plusieurs FlowFiles

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `join_strategy` | select | Oui | - | Stratégie (time, count, batch) |
| `join_timeout` | integer | Non | 60 | Timeout en secondes |
| `join_count` | integer | Non | 10 | Nombre de FlowFiles |

### 11.5. Tâches d'Analyse

#### 11.5.1. Aggregate Task (`aggregate`)
**Description** : Agréger plusieurs FlowFiles

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `aggregation_type` | select | Oui | - | Type (sum, count, avg, min, max, collect) |
| `field` | string | Non | - | Champ à agréger |
| `group_by` | array | Non | [] | Champs de groupement |

#### 11.5.2. Sort Task (`sort`)
**Description** : Trier les FlowFiles

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `sort_criteria` | json | Oui | - | Critères de tri |
| `order` | select | Non | ASC | Order (ASC, DESC) |

**Schema sort_criteria** :
```json
{
  "attribute1": "ASC",
  "attribute2": "DESC"
}
```

#### 11.5.3. Distinct Task (`distinct`)
**Description** : Supprimer les doublons

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `distinct_by` | array | Oui | [] | Attributs pour distinguer |
| `keep_first` | boolean | Non | true | Garder le premier ou dernier |

### 11.6. Tâches de Transformation

#### 11.6.1. JSON Task (`json`)
**Description** : Transformer/valider JSON

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (parse, validate, transform) |
| `transform_script` | textarea | Non | - | Script de transformation |
| `schema` | json | Non | - | Schéma JSON Schema |

#### 11.6.2. XML Task (`xml`)
**Description** : Transformer/valider XML

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (parse, validate, transform, xpath) |
| `xpath` | string | Non | - | Expression XPath |
| `schema` | xml | Non | - | Schéma XSD |

#### 11.6.3. CSV Task (`csv`)
**Description** : Transformer CSV

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (parse, format, convert) |
| `delimiter` | string | Non | , | Délimiteur |
| `has_header` | boolean | Non | true | Premier ligne est header |
| `quote_char` | string | Non | " | Caractère d'encadrement |

#### 11.6.4. Base64 Task (`base64`)
**Description** : Encoder/Décoder Base64

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `operation` | select | Oui | - | Opération (encode, decode) |

---

## 12. Référence Complète des Services

PyFi2 fournit 5 services partagés, accessibles dans les tasks via `self.get_service("service_id")`.

### 12.1. Database Connection Pool (`dbConnectionPool`)

**Fichier** : `services/db_connection_pool.py`
**Description** : Pool de connexions base de données (SQLite, PostgreSQL, MySQL via DB-API 2.0)

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `db_type` | string | Oui | sqlite | Type (sqlite, postgresql, mysql) |
| `database` | string | Oui | - | Chemin DB (SQLite) ou nom de la base |
| `host` | string | Non | localhost | Hôte (PostgreSQL/MySQL) |
| `port` | integer | Non | - | Port |
| `user` | string | Non | - | Utilisateur |
| `password` | secret | Non | - | Mot de passe |
| `pool_size` | integer | Non | 5 | Taille du pool |

**Utilisation dans une task** :
```python
db = self.get_service("my_db")
conn = db.get_connection()
cursor = conn.cursor()
cursor.execute("SELECT * FROM users")
```

### 12.2. Cache Service (`cacheService`)

**Fichier** : `services/cache_service.py`
**Description** : Cache en mémoire avec TTL et taille max

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `max_size` | integer | Non | 10000 | Nombre max d'entrées |
| `ttl` | integer | Non | 3600 | TTL en secondes |

**Utilisation** :
```python
cache = self.get_service("my_cache")
cache.put("key", "value")
val = cache.get("key")
```

### 12.3. HTTP Client Service (`httpClientService`)

**Fichier** : `services/http_client_service.py`
**Description** : Client HTTP partagé avec configuration de base

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `base_url` | string | Non | - | URL de base pour les requêtes |
| `timeout` | integer | Non | 30 | Timeout en secondes |
| `headers` | object | Non | {} | Headers par défaut |

### 12.4. LLM Connection (`llmConnection`)

**Fichier** : `services/llm_connection.py`
**Description** : Connexion aux LLMs (OpenAI, Anthropic) via HTTP natif (zero-dependency)

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `provider` | string | Oui | openai | Provider (openai, anthropic) |
| `api_key` | secret | Oui | - | Clé API |
| `model` | string | Non | gpt-4 | Modèle à utiliser |
| `base_url` | string | Non | - | URL de base personnalisée |
| `max_tokens` | integer | Non | 1024 | Tokens max par réponse |
| `temperature` | float | Non | 0.7 | Température |

**Utilisation avec InferLLM** :
```python
# Dans un flow JSON
"services": {
    "llm": {
        "type": "llmConnection",
        "provider": "openai",
        "api_key": "${LLM_API_KEY}",
        "model": "gpt-4"
    }
}
```

### 12.5. Distributed Map Cache Client (`distributedMapCache`)

**Fichier** : `services/distributed_cache.py`
**Description** : Cache distribué compatible NiFi DistributedMapCacheClient

**Paramètres** :
| Nom | Type | Requis | Défaut | Description |
|-----|------|--------|--------|-------------|
| `max_size` | integer | Non | 100000 | Taille max |
| `ttl` | integer | Non | 0 | TTL en secondes (0 = pas de TTL) |

**Utilisation** : Utilisé par les tasks `fetchDistributedMapCache` et `putDistributedMapCache`.

---

## 13. API du Moteur d'Exécution

### 13.1. Flow Executor (Batch)

```python
from engine import FlowExecutor

executor = FlowExecutor(
    max_workers=10,        # Threads parallèles
    max_retries=3,         # Retries par tâche
    flow_timeout=300,      # Timeout global (s)
    provenance=repo,       # ProvenanceRepository (optionnel)
)
result = executor.execute_flow(flow, input_flowfiles=[ff], variables={"key": "val"})
# result.success, result.duration_ms, result.statistics, result.errors
```

Séquence : tri topologique -> niveaux -> exécution parallèle -> clone si branching -> résultat.

### 13.2. ContinuousFlowExecutor (NiFi-style)

Exécution continue avec queues, backpressure et transactions :

```python
from engine.continuous_executor import ContinuousFlowExecutor

executor = ContinuousFlowExecutor(
    flow,
    max_workers=8,
    max_retries=3,
    enable_checkpoints=True,
    checkpoint_interval=30.0,
)
executor.start()
executor.inject(FlowFile(content=b"data"))
status = executor.get_status()   # task states, queue sizes
executor.stop()
```

**Modèle transactionnel** :
1. **Peek** : FlowFile lu de la queue (sans retirer)
2. **Execute** : tâche exécutée
3. **Commit** : FF retiré de l'entrée, résultats envoyés en sortie
4. **Rollback** : FF reste dans la queue, tâche -> ERROR

**Routing** : FlowFiles avec attribut `route.relationship` -> connection correspondante.
**Failure routing** : si connection "failure" existe -> FF déqueué et routé là.

**Hot-swap** :
```python
executor.update_task("task_id", new_config)    # Change config sans perte
executor.update_flow(new_flow)                  # Mise à jour structurelle
```

### 13.3. Scheduler (CRON)

```python
from engine.scheduler import FlowScheduler

scheduler = FlowScheduler()
scheduler.add_job("daily-etl", "flows/pipeline.json", "0 6 * * *")
scheduler.start()
scheduler.save_jobs()  # Persiste les jobs
scheduler.load_jobs()  # Restaure les jobs
```

### 13.4. API REST (FastAPI)

10 routeurs, 85+ endpoints. Documentation OpenAPI à `/docs`.

```bash
python -m api.app --port 8000
```

| Routeur | Préfixe | Description |
|---------|---------|-------------|
| auth | `/api/v1/auth` | Login, users, API keys, OAuth2, rôles |
| flows | `/api/v1/flows` | CRUD flows, validate, import/export |
| execution | `/api/v1/execution` | Batch, continu, inject, task actions |
| monitoring | `/api/v1/monitoring` | Bulletins, provenance, streaming |
| scheduler | `/api/v1/scheduler` | CRUD jobs CRON |
| tasks | `/api/v1/tasks` | Types, schémas de paramètres |
| workers | `/api/v1/workers` | Workers distants, health |
| plugins | `/api/v1/plugins` | Install/uninstall/upload/export |
| system | `/api/v1/system` | Health, info, security status |

**Auth** : Bearer token (session ou API key). Si auth désactivée, accès libre.

```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .session_id)

# Utiliser
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer $TOKEN"
```

---

## 14. GUI - Spécifications Techniques

### 14.1. Architecture GUI (Streamlit)

```
gui/
├── __init__.py
├── app.py                 # Point d'entrée principal
├── config.py             # Configuration Streamlit
├── editor/               # GUI de création
│   ├── __init__.py
│   ├── app.py           # Application editor
│   ├── canvas.py        # Canvas de flux
│   ├── properties.py    # Panneau propriétés
│   └── components/
│       ├── task_palette.py
│       ├── flow_editor.py
│       └── relation_editor.py
└── runtime/              # GUI de runtime
    ├── app.py           # Application runtime
    ├── dashboard.py     # Dashboard principal
    ├── flow_viewer.py   # Visualisation flux
    ├── logs.py          # Visualisation logs
    └── metrics.py       # Métriques temps réel
```

### 14.2. Écrans de l'Editor

#### 14.2.1. Page Principale
- Liste des flux existants
- Boutons Créer/Importer/Exporter
- Recherche et filtres

#### 14.2.2. Canvas de Flux
- Visualisation graphique des tâches
- Drag & drop des tâches depuis la palette
- Connexion des tâches par relations
- Zoom et navigation

#### 14.2.3. Panneau Propriétés
- Édition des paramètres de la tâche sélectionnée
- Validation en temps réel
- Aperçu des données

#### 14.2.4. Gestionnaire de Services
- Liste des services disponibles
- Création/Édition de services
- Test de connexion

### 14.3. Écrans du Runtime

#### 14.3.1. Dashboard Principal
- Vue d'ensemble des exécutions
- Statistiques globales
- Alertes et erreurs

#### 14.3.2. Visualisation des Flux
- État en temps réel des tâches
- Flux des données
- Métriques par tâche

#### 14.3.3. Logs Viewer
- Logs en temps réel
- Filtres et recherche
- Export des logs

#### 14.3.4. Configuration Runtime
- Override des variables
- Configuration des paramètres
- Déploiement des flux

---

## 15. Sécurité et Authentification (RBAC)

### 15.1. SecurityManager

```python
from core.security import SecurityManager

security = SecurityManager.get_instance()
security.enable_auth(True)

# Authentification
session = security.authenticate("admin", "password")
security.check_permission(session, "flow.edit")  # raises if denied

# API Keys
key = security.generate_api_key("My integration")

# OAuth2
security.set_oauth_config("google", {
    "client_id": "...", "client_secret": "...",
    "authorization_url": "...", "token_url": "..."
})
```

### 15.2. Rôles et Permissions

| Rôle | Permissions |
|------|-------------|
| **admin** | Tout : users, plugins, settings, flows, execute, monitor |
| **editor** | flows CRUD, execute, monitor, services |
| **operator** | execute, monitor |
| **viewer** | monitor (lecture seule) |

### 15.3. API REST Auth

L'API REST utilise un middleware qui supporte :
- **Bearer session token** : obtenu via POST /api/v1/auth/login
- **API key** : générée via la GUI ou l'API, donne accès admin
- **Mode désactivé** : si auth désactivée, tous les endpoints sont accessibles

---

## 16. Tests et Qualité

**758 tests**, tous verts.

```bash
pytest tests/ -v                    # Tous les tests
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=api --cov-report=term-missing
```

### 16.1. Fichiers de tests

| Fichier | Tests | Domaine |
|---------|-------|---------|
| test_executor.py | 23 | FlowExecutor batch |
| test_continuous_executor.py | 22 | ContinuousFlowExecutor |
| test_api.py | 39 | API REST |
| test_security_checkpoint.py | 29 | RBAC + Checkpoint |
| test_storage_backends.py | 30 | Git, SQLite, Filesystem, StorageManager |
| test_plugin_system.py | 21 | Plugins + export .pfp |
| test_streaming.py | 27 | FlowFile streaming + spill |
| test_new_io_tasks.py | 21 | XML, Email, Slack, SFTP |
| test_tasks.py | 15 | Tasks de base |
| ... | ... | ... |

### 16.2. Outils

- **pytest** pour les tests
- **pytest-cov** pour la couverture
- **FastAPI TestClient** pour les tests API

---

## 17. Déploiement et Production

### 17.1. Configuration Production

```yaml
# config/production.yaml
storage:
  type: postgres
  host: db.example.com
  port: 5432
  database: pyfi2
  
execution:
  max_workers: 50
  max_retries: 5
  timeout: 600
  
monitoring:
  enable_metrics: true
  enable_tracing: true
  log_level: INFO
```

### 17.2. Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install -r requirements.txt

CMD ["streamlit", "run", "gui/runtime/app.py"]
```

---

## 18. Services Filesystem

PyFi2 fournit une couche d'abstraction filesystem unifiée. Voir `docs/filesystem.md` pour le guide complet.

### 18.1. Types de services

| Type | Description | Git | Requis |
|------|-------------|-----|--------|
| `localFilesystem` | Relay HTTP vers machine user | Oui | Script `pyfi2_fs_relay.py` |
| `wsFilesystem` | Relay WebSocket | Oui | Script `pyfi2_fs_relay_ws.py` |
| `browserFilesystem` | File System Access API | Non | Chrome/Edge |
| `serverFilesystem` | Disque serveur (admin only) | Oui | Rôle admin |
| `googleDrive` | Google Drive REST API v3 | Non | OAuth2 |
| `oneDrive` | OneDrive Graph API | Non | OAuth2 |

### 18.2. Task `filesystemOps`

| Paramètre | Type | Requis | Description |
|-----------|------|--------|-------------|
| `service_id` | string | Oui | ID du service filesystem |
| `action` | string | Oui | list_dir, read_file, write_file, delete_file, mkdir, stat, exists, search, grep, find_replace, git_* |
| `path` | string | Non | Chemin relatif (défaut: ".") |
| `pattern` | string | Non | Pattern glob (search) ou regex (find_replace) |
| `regex` | string | Non | Pattern regex (grep) |
| `replacement` | string | Non | Texte de remplacement (find_replace) |
| `recursive` | boolean | Non | Récursif (search/grep, défaut: true) |

### 18.3. Permissions

- **Modes**: `read` (lecture seule), `readwrite` (lecture + écriture), `full` (+ suppression)
- **allowed_paths**: Préfixes autorisés (vide = tout)
- **denied_paths**: Préfixes interdits (prioritaire sur allowed)

### 18.4. Stockage OAuth tokens

`core/oauth_token_store.py` — Stockage chiffré des tokens OAuth par user/provider. Auto-refresh des access tokens expirés. Persistance dans `config/users/{user_id}/oauth_tokens.json`.

---

**Fin de la Documentation Technique**

*Version: 2.1.0*
*Date: 2026-03-14*
*70+ tasks, 11 services, 76+ filesystem tests, API REST, RBAC, Plugins, Docker*