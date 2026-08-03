# lawassist — PawFlow legal-assistant PFP packages (WIP)

Statut : implémentation initiale **versionnée dans le dépôt, mais encore WIP**.
Tout le code est autocontenu sous ce répertoire ;
aucun fichier du codebase PawFlow n'est modifié. Voir `docs/LEGAL_ASSISTANT_PLAN.md`
dans le dépôt principal pour l'analyse complète qui a produit ce plan
d'implémentation (rôles, garde-fous, RAG, modèles, business, backup).

Trois `.pfpdir` **indépendants** — aucune dépendance déclarée entre eux, donc
installables/désinstallables séparément, dans n'importe quelle combinaison :

| Package | Rôle | Dépend de |
|---|---|---|
| `firm.legal-assistant.pfpdir` | Métier avocat : 4 agents, garde-fous de citation, base SQLite légère, flows de suivi des échéances/digest | rien (aucune dépendance PFP) |
| `platform.doc-templates.pfpdir` | Templating de documents générique (mise en demeure, AR, convocation) | rien |
| `platform.incremental-backup.pfpdir` | Backup incrémental chiffré vers un stockage distant (Google Drive via le service `googleDrive` déjà natif à PawFlow, ou tout remote `rcloneFilesystem`) | rien |

Compose un déploiement à la carte : install standard PawFlow (déjà en place)
+ `firm.legal-assistant` + `platform.incremental-backup` (+ `platform.doc-templates`
si souhaité) — exactement la répartition demandée.

## Build et install (une fois le contenu relu)

```bash
/pfp key-create   # une seule fois, réutiliser la même paire de clés pour les 3 packages
# copier la clé publique retournée dans chaque pfp.json ("public_key")

/pfp build ./firm.legal-assistant.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp inspect ./firm.legal-assistant.pfpdir/dist/firm.legal-assistant-0.1.0.pfp
/pfp install ./firm.legal-assistant.pfpdir/dist/firm.legal-assistant-0.1.0.pfp --force

/pfp build ./platform.doc-templates.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp install ./platform.doc-templates.pfpdir/dist/platform.doc-templates-0.1.0.pfp --force

/pfp build ./platform.incremental-backup.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp install ./platform.incremental-backup.pfpdir/dist/platform.incremental-backup-0.1.0.pfp \
  --secret backup_passphrase=my_stored_passphrase_key --force
```

Ou en développement (non signé, itération rapide) : `/pfp dev-load ./firm.legal-assistant.pfpdir`.

## `firm.legal-assistant.pfpdir`

### Agents (4 rôles, section 12 du plan)

- `content/agents/assistant.json` — *"Retrouve, ne rédige pas."*
- `content/agents/collegue.json` — *"Relis et propose, ne décide pas."*
- `content/agents/secretaire.json` — *"Exécute le répétitif, jamais le sensible."*
- `content/agents/expert.json` — *"Cite, ne suppose jamais."*

Chacun a `assigned_skills: ["legal-citations", "legal-workflows"]`. Le garde-fou
commun (jamais d'affirmation de droit sans source, jamais de délai comme fait
acquis, jamais d'envoi sans validation humaine) est répété dans les 4 prompts
plutôt que factorisé, pour qu'il survive même si un agent est exporté seul.

### RAG juridique — installé par le .pfp, à activer par agent

Le type d'objet `.pfp` `mcp_server` (cf. `docs/PFP_PACKAGES.md` §"MCP Servers
(mcp_server)") installe directement une connexion MCP comme ressource `mcp`,
sans étape manuelle après install. Le plan (section 14) identifie
`https://justicelibre.org/mcp` (Streamable HTTP, gratuit, sans auth, ~3M
décisions + 1,75M articles versionnés) comme source RAG jour 1 ; ce package
la déclare via `content/mcp/legal-kb.json` + l'objet `mcp_server:legal-kb`
dans `pfp.json`, installée sous le nom `legal-kb` attendu par les 4 prompts
d'agent.
**Reste un geste explicite après install, par design** : les MCP sont opt-in
par conversation/agent (cf. `docs/tool_catalog.md` §Tool and MCP
Availability) — installer le package rend `legal-kb` disponible, il faut
encore la cocher pour les 4 agents (menu Ressources → MCP, ou override par
agent) avant qu'ils puissent l'interroger. C'est le même garde-fou
d'activation que n'importe quelle ressource MCP, pas une étape de
configuration en plus.

### Base structurée légère (section 19 du plan)

SQLite embarqué sur le relay, pas de service serveur additionnel :

- `content/flows/legal-db-init.json` — flow à un shot (trigger manuel via
  `generateFlowFile`), crée les tables `dossiers`, `delais`, `pieces` si
  absentes. À lancer une fois après install (`manage_flow` deploy + start,
  ou depuis le catalogue de flows du panneau Resources), avec le paramètre
  `db_path` pointé vers l'emplacement choisi (défaut suggéré :
  `/workspace/legal.db`, cohérent avec la racine de backup — voir
  `platform.incremental-backup`).
- Schéma détaillé : voir les commentaires SQL dans le flow lui-même.

### Flows de suivi (section 13/17 du plan)

- `content/flows/deadline-watch.json` — CRON quotidien (07:00) : lit les
  échéances actives à ≤30 jours depuis SQLite, construit un digest texte
  groupé J-1/J-7/J-30, envoie un email de rappel **à l'adresse du cabinet
  lui-même** (rappel interne, pas de communication à un tiers — cohérent
  avec le garde-fou §7.2). Ne crée ni ne modifie aucun événement calendrier
  automatiquement : la synchronisation calendrier reste un geste humain
  explicite via l'agent secrétaire (`manageCalendar`), pas un effet de bord
  du flow — plus sûr qu'un auto-sync silencieux pour un MVP.
- `content/flows/weekly-digest.json` — CRON hebdomadaire (lundi 08:00) :
  dossiers actifs, échéances à 7/14/30 jours, dossiers sans activité récente.
- **Paramètres SMTP à configurer à l'install** (mêmes conventions que la
  tâche `sendEmail` déjà présente dans PawFlow — OAuth2 ou identifiants
  classiques) : voir les paramètres `smtp_*`/`oauth2_*` en tête de chaque
  flow, à remplacer par de vrais secrets `${anonymous.xxx}` ou des valeurs
  directes selon la politique du cabinet.

`content/tasks/jurisprudence-watch.json` (task_def, tâche planifiée assignée
à un agent — `assign_task`, pas un flow) : veille périodique sur le MCP
legal-kb à partir de mots-clés/articles suivis, jamais de résumé sans citer
la décision exacte (garde-fou §7.1).

### Workflows sur demande (pas des flows CRON)

`content/skills/legal-workflows/SKILL.md` documente les workflows qui
demandent un jugement humain/LLM plutôt qu'un DAG rigide : nouveau dossier
(intake), relecture de brouillon, vérification de conflit d'intérêt,
préparation d'audience, assemblage de courrier (sections 3.3/13/17 du plan).
Assigné aux 4 agents ; chargé à la demande (`load_skill`).

## `platform.doc-templates.pfpdir`

- `content/tools/render_template/main.py` — tool PFP pur (aucun appel hôte,
  aucun `allowed_tools`/`allowed_services` requis) : substitue les
  placeholders `${...}` d'un texte de template à partir d'un objet de
  variables JSON. Variable manquante → marqueur explicite
  `[nom manquant]`, jamais une valeur inventée (garde-fou §20.2 du plan).
  Ne touche à aucun fichier — l'appelant (l'agent) décide où stocker le
  brouillon produit.
- `content/skills/doc-templates/templates/*.md` — 3 templates de départ (mise en demeure, accusé
  de réception, convocation RDV), éditables par le cabinet après install —
  copiés dans un espace éditable, jamais exécutés depuis le contenu signé
  (section 20.1 du plan ; l'espace éditable exact — filestore vs fichier
  relay — est un choix d'intégration à faire à l'usage, non figé ici).
- `content/skills/doc-templates/SKILL.md` — explique comment proposer des
  emplacements de variable (suggestion à valider, jamais automatique).

## `platform.incremental-backup.pfpdir`

Générique, indépendant du métier avocat (section 16.3 du plan) :

- `content/tools/incremental_backup/main.py` — parcourt une racine
  relay-locale (accès direct, pas de broker nécessaire — c'est du
  filesystem relay-local), calcule un manifeste `{chemin: (taille, sha256
  du **plaintext**, mtime)}`, compare au dernier manifeste connu côté
  destination, **chiffre systématiquement** (AES-256-GCM via la bibliothèque
  `cryptography` — dépendance à vérifier/installer dans l'environnement du
  relay si absente, cf. garde-fou §16.2bis du plan : le chiffrement n'est
  jamais optionnel), et n'upload que les fichiers nouveaux/modifiés via les
  tools génériques `write`/`read`/`list_dir`/`mkdir` avec le paramètre
  `destination`/`source` pointé sur le service configuré (`googleDrive` ou
  `rcloneFilesystem`, déjà natifs à PawFlow — rien à construire côté
  connecteur). Les noms de fichiers et le manifeste lui-même sont chiffrés ;
  les blobs sont stockés sous un nom opaque dérivé du hash
  (`_backup/blobs/<sha256>`).
- `content/tools/restore_from_backup/main.py` — chemin symétrique : lit un
  manifeste (dernier ou horodaté), déchiffre, retélécharge vers une racine
  cible.
- `content/tasks/nightly-backup.json` (task_def, planifié quotidien) —
  invoque `incremental_backup` avec des paramètres fixes (racines par
  défaut : `/workspace` du relay avec exclusions `.git`, `node_modules`,
  `__pycache__`, `.venv` — section 16.4 du plan) ; le rôle de l'agent ici
  est mécanique (un seul appel d'outil params fixes), pas un jugement.
- `content/skills/backup-operations/SKILL.md` — explique la restauration,
  le risque de perte de passphrase (§16.2bis : pas de `wrap_escrow` =
  sauvegarde irrécupérable si la passphrase est perdue), et pourquoi le
  contenu du MCP public justicelibre.org n'a jamais besoin d'être
  sauvegardé (déjà republié en source ouverte, section 16.4).

**Secret requis à l'install** : `backup_passphrase` (nom logique du
package) → lié à un secret PawFlow existant (`store_secret` puis
`--secret backup_passphrase=<clé_stockée>`). La clé de chiffrement (DEK) est
dérivée de cette passphrase via `scrypt` (stdlib `hashlib.scrypt`) et reste
**stable** tant que la passphrase ne change pas — condition nécessaire pour
que l'upload reste incrémental (section 16.2bis du plan : une DEK qui
changerait à chaque run casserait la déduplication).

**Non résolu, à valider avant usage réel** :

1. Le module `core/key_vault.py` (multi-wrap `pass`/`relay`/`escrow`) décrit
   dans le plan comme référence n'est **pas réutilisé tel quel** ici : il vit
   dans le codebase PawFlow, hors d'atteinte d'un entrypoint PFP sandboxé
   (voir `docs/PFP_DEVELOPER_GUIDE.md` — un PFP n'importe que le SDK `pfp`,
   pas les modules internes `core.*`). Ce package réimplémente donc son
   propre chiffrement scrypt+AES-GCM auto-contenu, avec le même principe
   (DEK stable, jamais en clair sur le disque distant) mais pas le même code
   — pas de `wrap_relay`/`wrap_escrow` hérité gratuitement comme espéré dans
   le plan d'origine.
2. Le passage de blobs binaires chiffrés à travers les tools génériques
   `write`/`read` (dont le paramètre `content` est documenté comme texte) est
   fait ici en base64 — comportement plausible mais **non vérifié contre
   l'implémentation réelle du bridge `write`** pour du contenu binaire à
   travers un service distant. À tester contre une vraie instance avant
   toute mise en production, avec un petit fichier de test.
3. `wrap_escrow` (clé de recouvrement détenue par un second associé) n'est
   pas implémenté dans ce premier jet — seule la passphrase simple
   (équivalent `wrap_pass`) existe. À ajouter si le cabinet l'exige.

## UI dédiée (section 5/9/18 du plan)

Deux nouveaux objets `.pfp` dans `firm.legal-assistant.pfpdir`, rendus
possibles par le type `web_app` (section 9.2 du plan — la capacité
plateforme manquante à l'époque a depuis été construite, voir
`docs/PFP_DEVELOPER_GUIDE.md` §"Standalone Pages") :

- `web_app:legal-shell` (`content/webapp/{index.html,app.js,style.css}`) —
  page dédiée servie à `/apps/firm.legal-assistant/legal-shell/` sous la
  même session authentifiée que `/chat` (pas de second login). Vanilla
  JS/CSS, aucune dépendance de build. Trois sections : dossiers/échéances
  actives (couleur J-1/J-7/J-30, jamais un fait acquis — la colonne
  "Confirmé" affiche explicitement "À VÉRIFIER" tant que
  `confirme_par_avocate` n'est pas coché en base), cartes d'accès rapide
  aux 4 agents, et réglage de l'écran par défaut (section 9.3 du plan).
  Cette page **satisfait l'exigence d'URL dédiée** posée en section 9 :
  installer le package fait apparaître un lien ↗ dans le panneau
  Ressources → Packages (mécanisme générique de `web_app`, aucun objet
  supplémentaire requis pour le "bouton dans l'interface principale").
- `ui_extension:legal-shell` — complète le `web_app` de deux façons :
  - une petite section repliable dans le panneau Resources du chat
    (`content/ui/legal_shell.js`, slot `resources_panel`) qui pointe vers
    le même lien `/apps/...` sans passer par l'onglet Packages ;
  - trois handlers serveur appelés directement en `fetch('/api/ui', {..,
    _ext: 'firm.legal-assistant'})` depuis `app.js` (pas de `pfp.call(...)`
    injecté sur une page `web_app`, cf. le guide développeur — l'appel HTTP
    direct est le chemin documenté) :
    - `legal.list_dossiers` — lit `dossiers`/`delais` dans `legal.db` en
      SQLite stdlib relay-local (aucun `allowed_tools` requis, cf. "Two
      trust boundaries" du guide développeur) ; erreur explicite si
      `legal-db-init` n'a pas encore tourné, jamais une donnée inventée.
    - `legal.get_default` / `legal.set_default` — préférence
      `default_conversation_id` par utilisateur, stockée dans
      `.legal_prefs.json` côté relay ; `set_default` synchronise aussi le
      cookie `pawflow_conv` lu par `/chat` et par les routes `web_app`
      elles-mêmes (`tasks/io/serve_pfp_webapp_assets.py`), donc le même
      réglage vaut pour les deux interfaces.
- **Non couvert par ce premier jet** : la page ne réagit pas encore
  automatiquement au cookie `default_conversation_id` au chargement (elle
  affiche le champ pré-rempli mais n'ouvre pas le chat toute seule) —
  laissé pour une itération suivante une fois l'usage réel du tableau de
  bord validé, plutôt que déjà scripté sans retour terrain.
- Build/inspect validés de bout en bout (`build_pfp` + `inspect_pfp` sur les
  13 objets, `status: new`, tous `installable: True`) ; suite ciblée
  `test_pfp_web_app`/`test_pfp_ui_extension`/`test_pfp_ui_handler`/
  `test_pfp_ui_security`/`test_pfp_package` : 254 tests, 0 régression.

## Ce qui n'est PAS encore fait

- Pas de fine-tuning (section 21) — hors scope MVP.
- `flow:conflict-check`, `flow:hearing-prep`, `flow:document-assembly`
  (section 17) sont couverts comme instructions de skill (jugement LLM),
  pas comme flows CRON séparés — cohérent avec la nature "sur demande" de
  ces workflows plutôt que planifiée.
- Rien n'est signé pour de vrai : les `pfp.json` utilisent
  `ed25519:REPLACE_WITH_PUBLIC_KEY`, à remplacer par une vraie clé
  (`/pfp key-create`) avant tout `/pfp build`.
