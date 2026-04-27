# Résumé du Projet PawFlow - État actuel

**Date de mise à jour** : 2026-04-27  
**Version package** : `1.0.0a1`  
**Statut** : alpha fonctionnelle, APIs encore susceptibles d'évoluer

## Synthèse

PawFlow n'est plus un simple MVP de moteur de workflow. Le dépôt contient maintenant une plateforme self-hosted d'orchestration d'agents IA et de pipelines, positionnée comme **"Apache NiFi meets Claude Code"** : un serveur PawFlow, un moteur de flows DAG, un système d'agents multi-providers, un relay local pour l'accès aux fichiers/outils, une interface web, un client terminal PawCode, une extension VS Code, de la documentation et une suite de tests conséquente.

Le coeur de valeur actuel est double :

1. **Agents autonomes outillés** : conversations multi-agents, providers LLM, tool-use loop, mémoire persistante, knowledge graph, diary agent, project graph, plans, délégation, streaming.
2. **Moteur de pipelines** : exécution DAG de FlowFiles, catalogue de tâches, triggers, backpressure, checkpoints, crash recovery, provenance et intégrations IO/data/control.

## Ce qui existe dans le dépôt

### Runtime et coeur Python

- `core/` : runtime agent et primitives principales.
  - exécution d'agents et boucles tool-use ;
  - providers LLM (`Claude Code`, `Codex CLI`, `Gemini CLI`, Anthropic API, OpenAI API, endpoints compatibles OpenAI selon configuration) ;
  - mémoire, knowledge graph, diary, project graph ;
  - gestion des conversations, plans, tokens, fichiers, relay et handlers d'outils ;
  - backends de stockage et helpers de sécurité/contexte.

- `engine/` : moteur de flows.
  - parsing et validation de flows JSON ;
  - exécution DAG ;
  - checkpoints, crash recovery, triggers, provenance ;
  - workers, scheduler, debugger, import NiFi et support cluster.

- `tasks/` : catalogue de tâches PawFlow.
  - `system/` : log, wait, fail, replace text, hash, scripts, cron trigger, génération/listing de FlowFiles, reporting ;
  - `io/` : HTTP, fichiers, SFTP/FTP, S3, GCS, Azure, Kafka, MQTT, email, Slack, Discord, Telegram, WhatsApp, web UI, relay, auth/session ;
  - `data/` : JSON, XML, CSV, SQL, extraction texte, transformations, compression, Avro/Parquet, base64, cache, déduplication ;
  - `control/` : routage, split/merge, rate limiting, ports, stop flow, execute flow, wait/notify ;
  - `ai/` : agent loop et modules associés à l'exécution agent.

- `services/` : services d'intégration et proxys.
  - authentification et providers OAuth ;
  - filesystem, terminal, browser, relay, gateway ;
  - services média/image/audio/vidéo, voix, 3D, desktop/browser et Pixazo ;
  - intégrations messaging et stockage.

### Interfaces et clients

- `cli.py` : CLI historique et point d'entrée `pawflow` déclaré dans `pyproject.toml`.
  - commandes de run/validate/list/info ;
  - démarrage API/UI ;
  - import, triggers, cluster, réindex mémoire.

- `pawflow_cli/` : **PawCode**, client terminal façon Claude Code.
  - mode interactif ;
  - compatibilité stream JSON ;
  - relay automatique du répertoire de travail ;
  - commandes terminal, contexte, fichiers et agent.

- `pawflow_relay/` : relay local/host.
  - expose fichiers, commandes shell et outils au serveur via WebSocket ;
  - permet au serveur d'agir sur la machine utilisateur sans accès direct au filesystem.

- `pawflow-vscode/` : extension VS Code TypeScript.
  - chat PawFlow dans VS Code ;
  - relay intégré ;
  - commandes sur sélection et contexte projet.

- `static/`, `pawflow-website/` et tâches `serve_*` : interface web, assets et site statique.

### Documentation

La documentation présente dans `docs/` couvre notamment :

- architecture interne ;
- système agent ;
- outils cognitifs : mémoire, KG, diary, project graph ;
- expression language ;
- slash commands ;
- catalogue de tâches ;
- déploiement Docker/local ;
- filesystem relay ;
- HTTP listener, provenance, Pixazo, voice clone ;
- développement de tâches/services.

Le `README.md` est aujourd'hui plus représentatif de la vision et de l'état courant que l'ancien résumé projet.

## Chiffres observés dans le dépôt

Ces chiffres décrivent l'état du dépôt au 2026-04-27, hors interprétation fonctionnelle fine :

| Zone | Volume observé |
|---|---:|
| Fichiers Python dans `core/` | 159 |
| Fichiers Python dans `engine/` | 20 |
| Fichiers Python dans `tasks/` | 131 |
| Fichiers Python dans `services/` | 63 |
| Fichiers de tests `tests/test_*.py` | 128 |
| Documents dans `docs/` | 19 |

Le README annonce aussi :

- 100+ types de tâches dans le catalogue ;
- 90+ outils intégrés ;
- 60+ slash commands dans le web chat ;
- 9 providers OAuth ;
- 2500+ tests.

## Fonctionnalités clés implémentées ou présentes

### Agents IA

- Conversations agent avec streaming.
- Tool-use loop et exécution d'outils via relay.
- Multi-agent et délégation.
- Plans structurés avec étapes, assignation et vérification.
- Mémoire persistante, semantic recall, knowledge graph, diary agent.
- Project graph basé sur AST/tree-sitter.
- Providers LLM multiples et endpoint compatible OpenAI.
- Modes de permission et contrôle d'accès aux outils selon configuration.

### Pipelines

- Flows JSON exécutés comme DAGs.
- FlowFiles, relations, paramètres et contexte runtime.
- Backpressure, checkpoints, reprise après crash.
- CRON, file watcher, webhook/polling/event triggers selon modules présents.
- Subflows, mapping de paramètres et import NiFi.
- Debugger de flow, provenance, versioning et mode cluster.

### Outils et relay

- Lecture/écriture/édition de fichiers.
- Bash/terminal via relay.
- Recherche de fichiers/contenu.
- Web fetch/scraping.
- Génération image, vidéo, audio, voix, 3D, upscale, try-on et lipsync selon providers configurés.
- Desktop/screen/browser automation via relay/VNC selon configuration.
- Scan sécurité et exécution de scripts.
- Gestion de secrets, ressources, mémoire, KG et plans.

### Interfaces utilisateur

- Web chat avec SSE, fichiers, contexte, slash commands, `/desktop` et gestion conversations.
- PawCode CLI pour usage terminal.
- Extension VS Code.
- Conversations partagées entre web, CLI, VS Code, API/channels et flows.
- Site statique de présentation.

### Authentification et déploiement

- Auth username/password et OAuth.
- JWT/API keys/RBAC selon modules présents.
- Déploiement local et Docker.
- Relay Docker ou natif.

## Points forts

1. **Ambition produit cohérente** : PawFlow combine agents autonomes et moteur de pipelines au lieu de rester un simple wrapper LLM.
2. **Architecture modulaire** : séparation nette entre core agent, engine, tasks, services, relay et clients.
3. **Surface d'intégration large** : fichiers, shell, web, messaging, cloud storage, bases de données, médias, OAuth.
4. **Approche self-hosted crédible** : le relay évite de donner au serveur un accès direct permanent au filesystem utilisateur.
5. **Outillage de continuité agent** : mémoire, KG, diary, plans et project graph vont au-delà d'un chat stateless.
6. **Couverture de tests significative** : le dépôt contient une vraie suite pytest, pas seulement un script de démonstration.

## Points de vigilance

- Le projet est explicitement en **alpha** : l'API publique, les formats JSON et les contrats internes peuvent encore bouger.
- La documentation n'est pas uniformément au même niveau de fraîcheur. Certains anciens documents décrivent encore une phase MVP.
- La surface fonctionnelle est très large : il faut distinguer les modules présents, les chemins testés et les intégrations réellement validées en production.
- Certaines capacités dépendent de secrets, providers externes, relay actif ou environnement Docker/local correctement configuré.
- Le README contient des chiffres de haut niveau utiles, mais ils doivent rester synchronisés avec le catalogue réel et les tests.

## Roadmap actuelle

D'après `ROADMAP.md`, les prochains axes importants sont :

- input voix push-to-talk ;
- isolation Git worktree pour agents parallèles ;
- providers LLM additionnels : Ollama, Mistral, vLLM, LM Studio, Together.ai ;
- MCP elicitation et exposition de PawFlow comme serveur MCP ;
- client mobile PWA ;
- éditeur visuel complet de flows ;
- assistant d'installation ;
- mode JSON headless ;
- marketplace agents/skills/tools/MCP/tasks/flows.

## Conclusion

PawFlow est passé d'une architecture de base à une plateforme agentique complète en alpha. Le résumé projet doit donc le présenter comme un système intégré : **serveur + agents + moteur de flows + relay + clients + documentation + tests**.

L'ancien message "4 tâches implémentées / 0 service / 1 script de test" est obsolète. La bonne lecture actuelle est : un produit déjà substantiel, avec une architecture riche et beaucoup de modules présents, mais qui doit encore stabiliser ses contrats, clarifier ce qui est production-ready et garder sa documentation synchronisée avec le code.
