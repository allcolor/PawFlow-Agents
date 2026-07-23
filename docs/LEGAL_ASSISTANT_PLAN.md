# PawFlow pour un cabinet d'avocat — analyse et propositions

> Document d'analyse produit. Pas un plan d'implémentation figé : sert de base
> de décision pour cadrer le MVP et les itérations suivantes.

## 1. Vision

L'objectif n'est pas un chatbot juridique générique, mais un système qui tient
quatre rôles à la fois pour l'avocate qui l'utilise :

- **Assistant** — retrouve l'information (texte de loi, pièce du dossier,
  historique d'un client) plus vite qu'elle ne le ferait seule.
- **Collègue** — relit un raisonnement, signale une contradiction, propose un
  angle qu'elle n'avait pas considéré, sans jamais décider à sa place.
- **Secrétaire** — gère les tâches répétitives et suivables : accusés de
  réception, prise de RDV, rappels, préparation de courriers types.
- **Expert** — connaît le droit applicable, cite les textes exacts avec leur
  version en vigueur, et sait dire "je ne sais pas" plutôt que d'inventer.

Le risque principal du projet n'est pas technique, il est de confiance : une
hallucination sur un article de loi ou un délai de procédure raté n'est pas
un bug UX, c'est une faute professionnelle. Toute l'architecture ci-dessous
est pensée autour de cette contrainte plutôt qu'en périphérie.

## 2. Ce qui existe déjà dans PawFlow et se réutilise tel quel

| Besoin | Brique PawFlow existante |
|---|---|
| Dossier client + historique | Une conversation par client (persistante, multi-session) |
| Contexte durable sur un dossier | Memory + Knowledge Graph scopés à la conversation |
| Notes d'entretien à l'oral | Chat vocal temps réel (OpenAI Realtime / Gemini Live, barge-in) |
| Envoi de courrier/relance | Tâche `sendEmail` (SMTP, OAuth2 Gmail/Microsoft 365) |
| Scraping de textes de loi / sites gouvernementaux | Tâche `fetchHTTP` / `scraplingFetch` (anti-bot, JS rendering) |
| Automatisations (relance auto, génération de courrier) | Moteur de flows (DAG, CRON, triggers) |
| Confidentialité | Self-hosted — aucune donnée client ne transite par un SaaS tiers par défaut |
| Pièces du dossier | Filesystem par conversation via le relay |

## 3. Ce qui manque et vient d'être construit

### 3.1 Tâche calendrier (`manageCalendar`) — livrée dans ce cycle

Nouvelle tâche `tasks/io/manage_calendar.py`, sur le même modèle que
`sendEmail` (mêmes conventions de credentials/erreurs) :

- **Provider `google`** — Google Calendar API v3, OAuth2 refresh-token
  (client_id/client_secret/refresh_token, identique au flow OAuth2 déjà
  utilisé pour Gmail). Actions `list` / `create` / `update` / `delete`.
- **Provider `caldav`** — CalDAV générique (Nextcloud, Radicale, iCloud, la
  plupart des serveurs auto-hébergés), auth Basic, `PUT`/`DELETE` de
  ressources iCalendar (.ics) et une requête `REPORT` (`calendar-query`) pour
  lister les événements sur une fenêtre de temps.

Couvert par 13 tests (mockés, sans appel réseau réel), documenté dans
`docs/tasks.md` et `CHANGELOG.md`.

Cela débloque directement les flows RDV et rappels décrits en section 5.

### 3.2 Base de connaissances juridique indexée

`fetchHTTP` à la demande est trop lent et fragile pour être la source
principale (rate limiting, mise en page qui change). Le bon pattern : un flow
CRON qui indexe périodiquement les textes pertinents (codes, jurisprudence
citée) dans la Knowledge Graph / memory avec embeddings, en conservant
systématiquement la source et la date de version. Le chat interroge cette
base locale en priorité, et ne re-scrape que pour vérifier une mise à jour ou
un texte absent.

### 3.3 Suivi des délais de procédure

La fonctionnalité à plus haute valeur (et plus haut risque si absente).
Structurellement : une entité "délai" par dossier (type d'acte, date de
déclenchement, durée légale, date butoir calculée, statut), stockée dans la
Knowledge Graph du dossier, avec un flow CRON quotidien qui scanne les délais
à venir et déclenche des rappels (`manageCalendar` + `sendEmail`/notification
in-app) à J-30, J-7, J-1. Le calcul de la date butoir elle-même doit être
vérifiable par l'avocate, jamais appliqué sans confirmation humaine.

## 4. Fonctionnalités proposées, par catégorie

### 4.1 Dossier client
- Fiche client (identité, coordonnées, type d'affaire, statut : actif / en
  attente de pièces / clôturé).
- Chronologie automatique du dossier reconstruite depuis les échanges,
  documents versés et actions effectuées.
- Recherche transversale ("tous les dossiers où on invoque l'article X").

### 4.2 Recherche juridique
- Réponse toujours sourcée : article exact, texte, date de version.
- Distinction claire entre "texte de loi vérifié dans la base indexée" et
  "synthèse générée à interpréter avec prudence" — marquage visuel différent.
- Veille juridique : alerte quand un texte suivi par le cabinet change.

### 4.3 Délais et procédure
- Calcul assisté de délai (jamais appliqué automatiquement).
- Vue "prochaines échéances tous dossiers" en page d'accueil.
- Rappels multi-canal (email, notification in-app, Telegram si le cabinet
  l'utilise déjà comme client agent).

### 4.4 Communication
- Brouillons de courrier générés à partir de templates + variables du
  dossier, jamais envoyés sans clic de validation humaine.
- Accusé de réception automatique sur les emails entrants identifiés comme
  liés à un dossier existant.
- Résumé automatique d'un échange email long avant de répondre.

### 4.5 Calendrier et RDV
- Prise de RDV client avec confirmation automatique (`manageCalendar` +
  `sendEmail`).
- Rappel 24h avant, avec possibilité de reprogrammer par lien.
- Vue calendrier globale du cabinet croisée avec les échéances de procédure.

### 4.6 Chat temps réel
- Dictée de notes d'entretien pendant ou juste après un RDV, transcrite et
  versée automatiquement dans la conversation du dossier concerné.
- Mode "collègue" : poser une question à voix haute sur un point de
  procédure pendant qu'on prépare un dossier, sans lâcher le clavier/dossier
  papier.

### 4.7 Rédaction assistée
- Premier jet de courrier, mise en demeure, conclusion — toujours présenté
  comme brouillon à revoir, jamais comme document final.
- Relecture de cohérence (dates, montants, noms de parties) entre le
  brouillon et les pièces du dossier.

### 4.8 Hors scope MVP (à ne pas construire tout de suite)
- Facturation / suivi du temps facturable — utile mais indépendant du cœur
  de la proposition, à traiter après le MVP si le besoin se confirme.
- Signature électronique — intégration tierce à évaluer séparément.

## 5. UI simplifiée dédiée

Pas le dashboard PawFlow actuel (pensé pour un opérateur technique), un skin
dédié réutilisant les mêmes API/SSE, avec trois écrans principaux.

### Écran 1 — Liste des dossiers
- Sidebar : recherche, tri par échéance la plus proche (pas par activité
  récente), badge rouge/orange sur les dossiers avec délai à moins de 7 jours.
- Chaque ligne : nom client, type d'affaire, statut, prochaine échéance.

### Écran 2 — Vue dossier
- Chat au centre (fil de conversation avec l'assistant sur ce dossier).
- Panneau latéral droit :
  - Pièces du dossier (upload, aperçu).
  - Échéances de ce dossier.
  - Actions rapides : envoyer un email, planifier un RDV, générer un
    courrier type — plutôt que de taper une commande.
- Distinction visuelle nette entre réponse de l'assistant sourcée (texte de
  loi vérifié) et brouillon/suggestion à valider.

### Écran 3 — Vue échéances transversale
- Calendrier global tous dossiers confondus — remplace l'agenda du cabinet,
  doit être la page d'accueil par défaut, pas une sous-page.
- Filtrage par type (RDV client / délai de procédure / audience).

## 6. Mapping des quatre rôles

| Rôle | Ce que ça veut dire concrètement ici |
|---|---|
| **Assistant** | Recherche instantanée dans la base juridique indexée et dans l'historique du dossier ; retrouve une pièce ou un échange en une phrase. |
| **Collègue** | Relit un brouillon de conclusion, signale une incohérence de date/montant, propose un angle transversal en repérant des dossiers similaires dans l'historique du cabinet — toujours en suggestion, jamais en décision. |
| **Secrétaire** | Accusés de réception, prise de RDV, rappels, préparation de courriers types — tout ce qui est répétitif et suivable par un flow. |
| **Expert** | Cite le texte exact en vigueur avec sa source, calcule un délai de procédure en le présentant comme "à vérifier", jamais comme un fait établi. |

## 7. Garde-fous non négociables

1. Toute affirmation de droit cite sa source exacte (article, texte, date de
   version) — jamais servie comme fait sans cette citation.
2. Aucun envoi automatique de courrier/email vers un tiers sans validation
   humaine explicite — les flows préparent, jamais n'expédient seuls.
3. Tout calcul de délai de procédure est présenté comme une proposition à
   vérifier, jamais appliqué ou communiqué sans confirmation humaine.
4. Confidentialité : vérifier que les LLM providers utilisés pour ce cabinet
   n'ont pas de rétention des prompts côté fournisseur si le contenu est
   sensible (provider local, ou engagement contractuel de non-rétention).

## 8. Ordre de priorité proposé (MVP → V1)

1. **Dossier-client + base juridique indexée avec citations** — sans ça, rien
   d'autre n'a de valeur différenciante.
2. **Suivi des délais de procédure** — plus haute valeur et plus haut risque
   si absent.
3. **Email semi-automatisé** — préparé, jamais envoyé sans clic.
4. **Calendrier/RDV** (`manageCalendar`, livré) — la brique la moins
   spécifique au droit, mais qui ferme la boucle secrétariat.
5. **UI dédiée** — une fois les flows validés en usage réel via le chat
   standard, pour éviter de construire une interface autour de
   fonctionnalités pas encore éprouvées.

## 9. Packaging en .pfp — URL dédiée, bouton, endpoint par défaut

Contrainte posée : ce livrable doit être un .pfp installable ; une fois
installé, l'interface avocat doit être joignable (a) via une URL dédiée et
(b) via un bouton dans l'interface PawFlow principale ; et l'utilisateur doit
pouvoir choisir, après connexion, un endpoint par défaut — depuis
l'interface PawFlow principale ou depuis l'interface dédiée elle-même.

### 9.1 Ce qui est déjà couvert par le format .pfp actuel

Le bouton dans l'interface principale ne demande rien de nouveau : un
ui_extension peut déclarer un slot action_menu ou header_actions (voir
docs/PFP_DEVELOPER_GUIDE.md, section UI Extensions) qui ouvre un lien ou un
panneau. Le reste du livrable (agents des quatre rôles, skill juridique,
task_def/flow pour le CRON de suivi des délais, service_definition pour
la base juridique indexée) rentre sans friction dans les types d'objets déjà
supportés (agent, skill, flow, task_def, service_provider, ui_extension).

### 9.2 Ce qui manque : pas de route page entière dédiée — **livré le 2026-07-23 (1.0.0-beta.30)**

> Mise à jour : le type d'objet `web_app` décrit ci-dessous comme préalable
> plateforme a été construit et livré (task `servePfpWebAppAssets`, route
> `/apps/<package>/<name>/`, bouton "Open {name}" dans le panneau Packages).
> Le reste de cette section 9.2 est conservé tel quel comme trace de
> l'analyse d'origine ; le blocage qu'elle décrit n'existe plus.

Le contrat ui.v1 des ui_extension (_UI_ASSET_EXTENSIONS dans
core/pfp_package/_pp_base.py) exclut délibérément .html : les assets d'une
extension s'injectent en JS/CSS dans la page chat existante (slots,
panneaux via pfp.ui.openPanel), toujours sur la même origine et le même
domaine de confiance que le chat. Il n'existe aujourd'hui aucun type d'objet
.pfp qui serve une page complète à sa propre URL (/legal/... ou
équivalent), séparée du shell chat. Une interface dédiée avec sa propre URL
n'est donc pas un détail d'implémentation ici : c'est une capacité PawFlow à
construire avant de pouvoir livrer ce .pfp.

Proposition minimale, cohérente avec le modèle de sécurité existant :

- Nouveau type d'objet installable, par exemple web_app : le manifeste
  déclare un dossier d'assets statiques (html/js/css) plus une route stable
  (/apps/<package>/). PawFlow sert ces fichiers derrière la même session
  authentifiée que le chat (cookie/token existant), donc pas de second login.
- Comme il s'agit d'une route séparée (pas d'injection dans le DOM du chat),
  servir du .html y est acceptable sans reproduire le risque documente pour
  ui_extension (tous les extensions partagent un même DOM/window) —
  mais ça doit rester examiné à l'install comme les autres objets
  code-porteurs (scan à l'inspect, taille/hash affichés, consentement
  explicite), et cette page ne doit pas pouvoir usurper l'origine du chat ni
  lire son DOM.
- Le bouton de l'écran principal (ui_extension / header_actions) devient
  alors un simple lien vers /apps/legal/..., pas un panneau injecté.

Tant que web_app n'existe pas, la seule solution livrable dès aujourd'hui
est de construire l'interface avocat comme panneau plein écran d'un
ui_extension (pfp.ui.openPanel, pas de nouvelle URL) — ce qui couvre le
bouton mais pas l'exigence explicite d'URL dédiée. Je recommande de traiter
web_app comme un petit chantier plateforme à faire d'abord, avant
d'attaquer le .pfp métier lui-même.

### 9.3 Endpoint par défaut après connexion

Lecture retenue : endpoint par défaut = le dossier (conversation) ou
l'agent sur lequel l'interface avocat s'ouvre automatiquement après
connexion, pas une URL d'API — l'interface dédiée reste un client de la même
instance PawFlow. Mécanisme proposé :

- Une préférence utilisateur côté serveur (pas côté navigateur seul, sinon
  elle ne survit pas au changement d'interface) : default_dossier_id (ou
  default_conversation_id) rattachée au compte utilisateur.
- Un handler serveur du ui_extension (pfp.call("legal.set_default", {...}),
  voir section Server handlers du guide développeur) qui lit/écrit cette
  préférence. Le même handler est appelé :
  - depuis un menu de l'interface PawFlow principale (gear_menu ou
    action_menu du même ui_extension) ;
  - depuis un écran de réglages de l'interface dédiée elle-même (une fois
    web_app disponible, via un appel au même endpoint serveur).
- Au chargement de l'interface dédiée après login, elle lit cette préférence
  et ouvre directement le bon dossier au lieu de la liste des dossiers
  (écran 1 de la section 5 devient l'écran de secours, pas le défaut).

### 9.4 Composition du .pfp

Un seul package, par exemple firm.legal-assistant :

- agent:* — les quatre rôles (assistant / collègue / secrétaire / expert)
  ou un agent unique avec assigned_skills couvrant les quatre postures.
- skill:legal-citations — discipline de citation systématique (garde-fou
  §7.1).
- flow:deadline-watch — CRON quotidien de suivi des délais (§3.3),
  task_def réutilisant manageCalendar + sendEmail.
- service_definition:legal-kb — base juridique indexée (§3.2).
- ui_extension:legal-shell — bouton d'accès + réglage d'endpoint par
  défaut, plus (une fois disponible) l'objet web_app pour la page dédiée
  elle-même.
- Secrets déclarés : credentials manageCalendar/sendEmail, clé du
  provider LLM si non-rétention contractuelle exigée (garde-fou §7.4).

## 10bis. Compléments d'analyse (2026-07-23) — modèles, RAG, MCP, business

Ce qui suit prolonge l'analyse ci-dessus avec des éléments vérifiés par
recherche web (prix, disponibilité d'API, sources de données réelles), à la
demande explicite de creuser agents/prompts/workflows/modèles/coûts/prix de
vente/RAG/MCP. Chiffres de prix précis marqués "à confirmer" quand la source
ne les publie pas noir sur blanc — cohérent avec le garde-fou §7 : on ne sert
pas une affirmation non vérifiée comme un fait.

### 11. Modèles candidats et coûts

Deux familles open-weight actuellement compétitives pour un usage agentique
long-contexte, toutes deux avec function-calling et support MCP natif côté
API (confirmé pour GLM-5.2 dans sa doc officielle : "peut appeler librement
des outils MCP externes") :

| | GLM-5.2 (Z.ai / Zhipu) | Kimi K3 (Moonshot AI) |
|---|---|---|
| Contexte | 1M tokens (long-horizon réel, pas juste annoncé) | 1M tokens |
| Positionnement éditeur | Agent/coding long-horizon, proche Opus 4.7-4.8 sur les benchmarks SWE cités | "Built for Agentic Coding & Knowledge Work" — le "knowledge work" du slogan correspond bien à la posture collègue/expert visée ici |
| Facturation | API au token (open.bigmodel.cn) **ou** abonnement forfaitaire "GLM Coding Plan" (pensé coding-agent, à vérifier si le forfait couvre un usage agent généraliste au volume du cabinet) | API au token uniquement trouvé (rechargement de crédit, pas de forfait mensuel identifié) |
| Hébergement | Chine (Zhipu) — soulève la question de rétention pour du contenu sensible | Chine (Moonshot) — même remarque |
| Poids ouverts | Oui pour les générations précédentes (GLM-4.x) — à vérifier au cas par cas si la version exacte déployée est auto-hébergeable via Ollama/vLLM | Lignée K2 diffusée en poids ouverts — à vérifier pour la version exacte utilisée |

Tarifs par million de tokens : non retrouvés en clair sur les pages
publiques consultées (documentation en chinois, tableaux de prix rendus en
JS). Avant tout engagement cabinet, obtenir les tarifs à jour directement
depuis le tableau de bord facturation (open.bigmodel.cn "价格" / Moonshot
"Model Pricing") plutôt que de les estimer ici. Ce qui est vérifiable sans
ambiguïté : les deux sont des modèles ouverts chinois nettement moins chers
que les modèles fermés occidentaux haut de gamme à capacité comparable — de
l'ordre de plusieurs fois moins cher par token, ce qui change la conversation
prix de vente (section 15).

**Routage par rôle plutôt qu'un modèle unique pour les quatre rôles :**

- **Secrétaire** (accusés de réception, RDV, rappels) — tâche mécanique, pas
  de contenu juridique sensible : modèle le moins cher/rapide disponible, ou
  un LLM service marqué `subscription` (coût virtuel, cf. le système de
  suivi de coûts livré en beta.29) si le cabinet a déjà un abonnement plat
  ailleurs.
- **Assistant** (recherche factuelle dans la base indexée) — la précision
  vient du RAG (section 14), pas de la mémoire du modèle : un modèle rapide
  suffit tant que le prompt impose de ne répondre qu'à partir des passages
  récupérés.
- **Collègue** (relecture, angle transversal) — usage ponctuel, pas continu :
  justifie le modèle flagship (GLM-5.2 ou Kimi K3) même si plus cher au
  token, car le volume réel par dossier reste faible.
- **Expert** (citation de loi, calcul de délai) — l'exigence n'est pas "le
  meilleur modèle" mais "zéro citation non sourcée" : la discipline imposée
  par `skill:legal-citations` (refuser de répondre sans passage retrouvé)
  compte plus que le choix du modèle lui-même.

**Confidentialité et souveraineté des données** (garde-fou §7.4) : pour les
dossiers les plus sensibles (pénal, secret des affaires), trois options
non-exclusives à documenter et laisser au choix du cabinet, par dossier :

1. Auto-hébergement via Ollama/vLLM d'un modèle ouvert sur le matériel du
   cabinet (aucune donnée ne sort jamais) — au prix d'une qualité inférieure
   au flagship hébergé, et d'un investissement matériel (GPU) à amortir.
2. Engagement contractuel de non-rétention avec le fournisseur d'API choisi.
3. Fournisseur occidental alternatif si le cabinet l'exige par principe,
   même à coût supérieur — PawFlow reste multi-provider par conception, ce
   choix ne doit jamais être figé en dur dans le .pfp.

Avant d'arrêter un choix définitif : instrumenter un mois pilote via le
ledger d'usage déjà livré (`core/usage_ledger.py`) avec un budget plafond
par rôle (`core/budget_store.py`, policy `block`) plutôt que d'estimer les
coûts a priori.

### 12. Types d'agents et prompts — exemples concrets

Quatre agents distincts (ou un agent unique avec quatre `assigned_skills`,
selon la préférence d'implémentation) partageant le même garde-fou de base :

**Garde-fou commun (system prompt, injecté aux quatre) :**
> Tu assistes une avocate, tu ne la remplaces jamais. Toute affirmation de
> droit doit citer sa source exacte (article, texte, date de version) telle
> que retrouvée dans la base documentaire — si tu ne trouves pas la source,
> dis-le explicitement plutôt que de répondre de mémoire. Tu ne calcules
> jamais un délai de procédure comme un fait acquis : présente-le toujours
> comme "à vérifier par l'avocate". Tu ne déclenches jamais l'envoi d'un
> email ou d'un courrier vers un tiers sans validation humaine explicite.

**Assistant** — *"Retrouve, ne rédige pas."*
> Ton rôle : recherche documentaire instantanée dans la base juridique
> indexée et l'historique du dossier en cours. Réponds par les passages
> exacts retrouvés, avec leur source, jamais par une reformulation qui
> pourrait diverger du texte. Si la question sort du dossier courant,
> précise que la réponse vient d'une recherche transversale.

**Collègue** — *"Relis et propose, ne décide pas."*
> Ton rôle : relire un brouillon ou un raisonnement, signaler toute
> incohérence (dates, montants, noms de parties) entre le texte et les
> pièces du dossier, et proposer un angle non envisagé en t'appuyant sur des
> dossiers similaires du cabinet si pertinent. Formule toujours tes retours
> comme des suggestions numérotées, jamais comme des corrections appliquées.

**Secrétaire** — *"Exécute le répétitif, jamais le sensible."*
> Ton rôle : accusés de réception, prise de RDV (`manageCalendar`), rappels
> d'échéance, préparation de courriers types à partir de templates. Tu
> prépares toujours un brouillon ; l'envoi effectif (`sendEmail`) attend un
> clic de validation humaine, sans exception.

**Expert** — *"Cite, ne suppose jamais."*
> Ton rôle : donner le texte de loi exact en vigueur à la date pertinente,
> avec sa source et sa version. Pour un calcul de délai de procédure,
> détaille le raisonnement (acte déclencheur, durée légale, méthode de
> calcul) mais conclus systématiquement par "à vérifier avant toute action".

### 13. Workflows détaillés

**`flow:deadline-watch`** (CRON quotidien, déjà esquissé en §3.3/§8) :
1. Scanner la Knowledge Graph pour les entités "délai" à échéance ≤ 30 jours.
2. Pour chaque délai : mettre à jour/créer l'événement calendrier
   (`manageCalendar`) et préparer (jamais envoyer) un rappel par email.
3. Notifier in-app à J-30 / J-7 / J-1, avec lien direct vers le dossier.
4. Aucune étape n'envoie ou ne modifie une date sans que l'avocate l'ait
   confirmée au moins une fois à la création du délai.

**`flow:new-matter-intake`** (nouveau dossier) :
1. Déclenchement manuel ou vocal ("nouveau dossier pour M./Mme X").
2. Agent d'extraction structure les entités de base (client, type d'affaire,
   dates connues) dans la Knowledge Graph, scopée à une nouvelle conversation.
3. Rattachement automatique du `skill:legal-citations` et des agents des
   quatre rôles à cette conversation.
4. Si des dates de procédure sont identifiées dès l'intake, création des
   entités "délai" correspondantes (déclenche `deadline-watch` dès le
   prochain passage CRON) — toujours présentées à valider, jamais actées
   automatiquement (cf. garde-fou §7.3).

**`flow:draft-review`** (relecture collègue) :
1. Déclenchement manuel depuis l'écran dossier ("relire ce brouillon").
2. L'agent collègue charge le brouillon + les pièces du dossier (dates,
   montants, noms) depuis la KG et le filesystem du dossier.
3. Retour structuré : liste numérotée d'incohérences + suggestions d'angle,
   jamais une édition directe du fichier.

### 14. RAG de départ, sources de données, MCP

Découverte de recherche qui change la section 3.2/9.4 d'origine : un serveur
MCP public, gratuit, sans authentification existe déjà et couvre exactement
le besoin de base juridique versionnée — **justicelibre.org**
(`https://justicelibre.org/mcp`, protocole Streamable HTTP). Chiffres
affichés par le service lui-même : ~3M décisions indexées, 1,75M articles de
loi avec versions historiques, 30 outils MCP couvrant juridictions
administratives (CE, 9 CAA, 40 TA), judiciaires (Cass, CA, Conseil
constitutionnel), et européennes (CEDH, CJUE) — plus JORF, conventions
collectives (KALI), délibérations CNIL. Fondement légal : lois Open Data de
2016/2019, Licence Ouverte 2.0 Etalab (réutilisation libre, citation de
source seule condition).

Point qui valide directement le garde-fou §7.1 ("jamais de citation sans
version en vigueur à la date exacte") : l'outil `get_law_article(code, num,
date)` restitue la rédaction d'époque d'un article, pas sa version actuelle
— exactement l'anachronisme juridique à éviter en jurisprudence. Point de
repère prix trouvé en recherche : le service indique que "c'est exactement
cette garantie que Dalloz vous facture 200€ par mois" — donnée non vérifiée
de façon indépendante mais plausible comme ancrage tarifaire (section 15).

**Réserve trouvée en recherche, à documenter dans le .pfp** : la
jurisprudence judiciaire "temps réel" (dernières décisions Cour de
cassation/cours d'appel via Judilibre) reste derrière une authentification
OAuth2 obligatoire côté plateforme PISTE — justicelibre.org ne la contourne
pas, il sert un miroir DILA mis à jour hebdomadairement pour cette partie
(`search_judiciaire_libre`) et documente lui-même une procédure
d'inscription PISTE en 13 étapes pour qui veut le flux temps réel
(`search_judiciaire`, `get_decision_judiciaire`). La justice administrative,
elle, est ouverte et temps réel sans aucune authentification. Un recours
citoyen contre ce verrou judiciaire est en cours selon le service — à
surveiller, pas à attendre pour livrer le MVP.
**Plan de bootstrap RAG révisé (remplace le CRON d'indexation générique
prévu en §3.2/§9.4 comme *premier* réflexe) :**

1. **Jour 1, coût nul** — relier le `service_provider`/MCP legal-kb du .pfp
   directement à `https://justicelibre.org/mcp` comme ressource MCP externe
   liée aux agents. Couvre d'emblée la quasi-totalité du besoin "texte de loi
   + jurisprudence sourcée et versionnée" sans infrastructure de scraping à
   construire ni maintenir.
2. **Si la fraîcheur temps réel de la jurisprudence judiciaire compte pour
   le cabinet** — lancer l'inscription PISTE (délai de traitement à prévoir,
   selon le tutoriel documenté par justicelibre.org) en parallèle, sans que
   ça bloque la livraison du MVP qui fonctionne déjà avec le miroir
   hebdomadaire.
3. **Ce qui reste un vrai chantier RAG interne** — l'historique propre du
   cabinet (dossiers passés, conclusions, mémos internes) : c'est la seule
   partie qui n'a pas d'équivalent public, et la seule qui doit
   impérativement rester sur l'infrastructure du cabinet (jamais envoyée à
   un MCP public). Reprendre ici le plan d'indexation par embeddings + KG
   de la section 3.2 d'origine, mais scopé à ce contenu interne uniquement.
4. **Recontacter la question du modèle d'embeddings** une fois ce périmètre
   interne clarifié — lui seul justifie un éventuel auto-hébergement dédié
   (section 11) puisqu'il touche des pièces client non publiques.

### 15. Modèle économique — comment vendre, quel prix

**Ce que la recherche confirme sur le marché du haut de gamme** : Harvey AI
(licorne valorisée 11 Md$) a ouvert un bureau à Paris en mai 2026 avec des
clients comme Bredin Prat et CMS Francis Lefebvre — du très grand cabinet,
vente enterprise sur devis, aucun prix public trouvé (cohérent avec le
fonctionnement habituel de ce segment : cycle de vente commercial, pas de
grille tarifaire affichée). CoCounsel (Thomson Reuters) suit le même schéma.
Ce segment n'est pas une cible réaliste pour un livrable self-hosted type
PawFlow — c'est un marché de vente enterprise que PawFlow n'a ni la force de
vente ni la légitimité de marque pour disputer frontalement.

**Le segment non couvert** : cabinets boutique/solo, qui n'ont ni le budget
ni le besoin d'une vente enterprise à 11 Md$ de valorisation en face. Aucun
acteur self-hosted/source-available sérieux identifié sur ce créneau précis
dans les recherches menées — c'est là que la proposition PawFlow (MIT,
auto-hébergé, coût LLM transparent au token via le ledger déjà livré) a un
angle réel plutôt que de concurrencer Harvey sur son propre terrain.

**Structure de prix proposée** (pas un prix noir-boîte à la Harvey/CoCounsel
— justement parce que la confiance est la contrainte n°1 du projet, cf.
section 1) :

1. **Frais de mise en place + package .pfp** — installation, indexation de
   l'historique propre du cabinet, connexion PISTE si besoin, calibrage des
   garde-fous/prompts — un forfait de service, pas une licence par siège.
2. **Coût d'inférence LLM séparé et transparent** — le cabinet apporte sa
   propre clé API ou son abonnement, le ledger d'usage (déjà livré en
   beta.29) l'itemise nativement ; PawFlow n'absorbe pas ce risque de coût
   variable et ne le maquille pas dans un forfait opaque — argument de
   confiance direct pour une profession qui vérifie tout.
3. **Paliers par taille de cabinet** — solo/boutique (1-3 avocats) : forfait
   d'installation + support optionnel à la demande. Petit cabinet (4-15) :
   mêmes briques + fonctions multi-utilisateur (échéances transversales,
   rôles multiples) avec un forfait et/ou abonnement de support plus élevé.
   Au-delà, c'est le segment où Harvey a déjà la relation de vente
   enterprise — ne pas chercher à y suivre.

**Ordre de grandeur** (estimation explicitement non vérifiée, à cadrer avec
de vrais devis avant publication) : le repère Dalloz à 200€/mois pour une
seule fonctionnalité adjacente (citation versionnée) suggère qu'un cabinet
boutique a déjà un budget "legal-tech" de cet ordre — un positionnement
crédible viserait un total (forfait + coût LLM réel, hors le segment
enterprise) sensiblement inférieur au coût mensuel d'une heure de
paralegal, tout en restant dans cette gamme de budget déjà acceptée par le
marché pour un outil adjacent.

**Recommandation de mise en marché** : piloter avec 1-2 cabinets partenaires
à tarif réduit/gratuit contre retour d'usage et étude de cas, avant de fixer
une grille tarifaire publique — cohérent avec l'ordre de priorité MVP→V1
déjà posé en section 8.

## 16. Backup incrémental — brique séparée du package métier

Demande explicite : une solution de backup incrémental (cible type Google
Drive), et pouvoir composer un déploiement à partir de trois briques
indépendantes : **install standard PawFlow + `.pfp` avocat + `.pfp` backup**.
Cette dernière contrainte est aussi importante que la fonctionnalité
elle-même — le backup ne doit être ni un module interne du package avocat,
ni couplé à son cycle de vie.

#### 16.1 Ce qui existe déjà et se réutilise tel quel

Deux services de destination sont déjà enregistrés, aucun n'est à
construire :

- `googleDrive` (`services/gdrive_filesystem_service.py`) — accès natif
  côté serveur à Google Drive via l'API REST v3 (OAuth2, scope `drive`),
  implémente l'interface `FilesystemBackend` standard (list/read/write/
  mkdir/stat/exists). C'est la cible "par exemple gdrive" demandée,
  disponible sans rien écrire.
- `rcloneFilesystem` (`services/rclone_filesystem_service.py`) — config
  d'un remote rclone (drive, s3, onedrive, gcs, azureblob, webdav, sftp,
  ftp), monté côté relay sous `/remote/<service_id>`. Élargit la cible à
  peu près n'importe quel stockage si le cabinet préfère S3/OneDrive/un
  NAS auto-hébergé plutôt que Drive.

Ce qui manque, vérifié par grep sur tout le dépôt : aucune tâche/flow
n'implémente aujourd'hui de **sauvegarde incrémentale** au sens propre
(manifeste des fichiers déjà sauvegardés + upload des seuls fichiers
nouveaux/modifiés). `filesystemOps` (`tasks/io/filesystem_ops.py`) sait
faire `read_file`/`write_file`/`mkdir` fichier par fichier mais ne fait
aucun diff ni suivi d'état entre deux passages — la brique à construire est
cette couche de diff, pas l'accès au stockage distant.

#### 16.2 Conception proposée

**`task_def:incrementalBackup`** (nouvelle tâche, même convention que les
tâches io existantes) :

1. Parcourt une racine source (chemin relay, ou dossier filestore d'une
   conversation) et construit un manifeste `{chemin: (taille, sha256,
   mtime)}`.
2. Lit le dernier manifeste connu à la destination
   (`_backup/manifest.json` via le `service_id` configuré — `googleDrive`
   ou un chemin monté `rcloneFilesystem`).
3. Calcule le diff : upload uniquement des fichiers nouveaux ou dont le
   sha256 a changé ; les fichiers identiques ne retraversent jamais le
   réseau — c'est la définition même de "incrémental".
4. Écrit un nouveau manifeste horodaté (`_backup/manifests/<horodatage>.json`)
   et met à jour `_backup/manifest.json` (pointeur vers le dernier état
   connu), pour permettre un retour à un point dans le temps, pas
   seulement au dernier état.
5. Purge optionnelle : garder N manifestes ou supprimer ceux plus vieux que
   X jours (paramètre de rétention), jamais les fichiers de données
   eux-mêmes tant qu'ils sont référencés par un manifeste conservé.

**`flow:incremental-backup`** — CRON (quotidien ou nocturne, paramétrable),
sur le même modèle que `flow:deadline-watch` : un seul paramètre de flow
pointe le `service_id` de destination, ce qui permet au même package
d'écrire vers Drive pour un cabinet et vers S3/OneDrive pour un autre sans
toucher au flow lui-même.

**Restauration** — tâche symétrique `task_def:restoreFromBackup` (ou un mode
`restore` de la même tâche) : lit un manifeste choisi (dernier ou
horodaté), retélécharge chaque fichier référencé vers une racine cible.
Comme les deux services de destination exposent déjà `read_file` en plus de
`write_file`, cette lecture ne demande aucune nouvelle capacité côté
service, seulement la tâche de restauration elle-même.

#### 16.2bis Chiffrement des backups — par défaut, pas en option

Suite à relecture explicite de ce point : pour un cabinet d'avocat, laisser
le chiffrement en option revient à accepter par défaut qu'une sauvegarde
soit le point de fuite le plus faible du système — incohérent avec le
garde-fou §1 (la confiance est la contrainte n°1) et §7.4. Décision proposée
: **`task_def:incrementalBackup` chiffre systématiquement, sans option pour
le désactiver.** Google Drive/S3/OneDrive restent des sous-traitants tiers
au sens RGPD ; sans chiffrement côté client, le fournisseur cloud voit les
pièces du dossier en clair, ce qui contredit la promesse self-hosted du
reste de PawFlow (section 2, ligne "Confidentialité").

**Ne pas inventer un second mécanisme crypto : réutiliser celui déjà conçu
pour le chiffrement au repos.** `docs/design/encryption-at-rest.md` (statut
DESIGN/RFC, pas encore codé) et son commencement d'implémentation
`core/key_vault.py` posent déjà exactement le bon modèle : une DEK
(32 octets aléatoires, AEAD AESGCM/ChaCha20Poly1305) qui chiffre les
données, elle-même enveloppée ("wrap") par une KEK dérivée par scrypt d'une
passphrase détenue par le cabinet — la clé ne touche jamais le disque en
clair et n'est jamais transmise à PawFlow ni au fournisseur cloud. Le
format multi-wrap (`pass` / `relay` / `escrow`) est déjà pensé pour porter
plusieurs portes vers la même DEK. Réutiliser ce module pour le backup :

- Cohérence UX : si le cabinet chiffre déjà ses conversations sensibles au
  repos avec une passphrase, le backup peut déverrouiller la même DEK par
  scope plutôt que d'imposer une seconde passphrase à retenir.
- Le module est encore au statut RFC/phase 1 (`wrap_pass` seul implémenté) —
  le package `platform.incremental-backup` peut consommer `core/key_vault.py`
  tel quel dès aujourd'hui pour `wrap_pass`, et hérite de `wrap_relay`/
  `wrap_escrow` gratuitement quand ces phases livreront, sans changer son
  propre format de manifeste.

**Piège à éviter — la DEK doit être stable, pas une par run.** Un backup
incrémental fonctionne parce que le sha256 d'un fichier inchangé est
identique d'un passage à l'autre ; ce sha256 doit donc se calculer **sur le
plaintext**, avant chiffrement, pour rester stable. Si la tâche mintait une
nouvelle DEK à chaque exécution (ou si l'AEAD réutilisait un nonce
aléatoire sans autre précaution), le ciphertext d'un fichier identique
diffèrerait à chaque run et l'upload incrémental perdrait tout son intérêt
(tout reuploadé, tout le temps). Conception correcte : une DEK persistante
par scope de backup (par cabinet, ou par dossier si le cabinet veut des clés
séparées par affaire), déverrouillée en RAM au démarrage du flow CRON via
`KeyVault`, jamais reminée à chaque passage.

**Les métadonnées aussi, pas seulement le contenu.** Chiffrer le contenu
d'un fichier mais laisser son nom et son chemin en clair sur Drive ("M.
Dupont - mise en demeure.docx") fuite déjà l'identité du client au
fournisseur cloud. Le manifeste (chemin → hash/mtime/taille) doit lui-même
être chiffré par la même DEK, et les objets stockés côté cloud sous un nom
opaque dérivé du hash (`_backup/blobs/<sha256>`) plutôt que sous le chemin
d'origine — la correspondance chemin réel ↔ nom opaque ne vit que dans le
manifeste déchiffré.

**Custody et risque de perte** — à documenter noir sur blanc au cabinet
avant activation, pas seulement dans la doc technique : si la passphrase
(`wrap_pass`) est perdue et qu'aucun `wrap_escrow` n'a été configuré, la
sauvegarde est définitivement irrécupérable — c'est la garantie même du
modèle (ni PawFlow ni l'hébergeur ne peuvent déverrouiller sans elle), mais
appliquée à un backup ça veut dire "la roue de secours est aussi crevée si
on perd la clé". Recommandation : proposer `wrap_escrow` (clé de recouvrement
détenue par un second associé ou un tiers de confiance du cabinet, hors
PawFlow) comme option explicite à l'activation du package, jamais comme
défaut silencieux — un escrow mal choisi réintroduit exactement le risque de
fuite que le chiffrement visait à éliminer.

La restauration (`task_def:restoreFromBackup`) demande donc la même
passphrase/KEK que la sauvegarde — sans elle, seuls des blobs chiffrés sous
noms opaques sont récupérables, ce qui est le comportement voulu.

#### 16.3 Packaging — répartition en trois briques indépendantes

Point clé de la demande : ne pas coupler le backup au package avocat.
Le modèle de dépendances `.pfp` (`docs/PFP_PACKAGES.md` §dependencies)
supporte nativement des packages installés côte à côte sans lien entre eux,
ce qui donne exactement la répartition demandée :

1. **Install standard PawFlow** — le socle (serveur, moteur de flows,
   services `googleDrive`/`rcloneFilesystem` déjà dans le cœur, pas dans un
   package).
2. **`.pfp` `firm.legal-assistant`** — le métier avocat (section 9.4),
   installé ou non indépendamment du backup.
3. **`.pfp` `platform.incremental-backup`** — package séparé et générique
   (pas spécifique au droit), composé de :
   - `task_def:incrementalBackup` + `task_def:restoreFromBackup`,
   - `flow:incremental-backup` (CRON, `service_id` en paramètre),
   - pas de `service_definition` propre : il consomme un `googleDrive` ou
     `rcloneFilesystem` déjà configuré au niveau de l'instance, pour éviter
     de dupliquer des credentials entre packages,
   - secrets déclarés : aucun credential propre — référence le
     `service_id` de destination choisi à l'install.

Aucune dépendance déclarée dans un sens ou dans l'autre entre
`firm.legal-assistant` et `platform.incremental-backup` : les deux
s'installent, se mettent à jour et se désinstallent indépendamment. Un
cabinet peut vouloir le backup seul (sans le métier avocat) ou l'inverse ;
le réutiliser tel quel pour n'importe quel autre package métier futur est
le but explicite de le garder générique.

#### 16.4 Ce que ce package sauvegarderait pour le cas d'usage avocat

Si les deux packages sont installés ensemble pour un cabinet, la
configuration recommandée du flow cible plusieurs racines source, chacune
déclarée comme une entrée de la liste `sources` du flow (le paramètre
`service_id`/racine peut être répété, une paire source→manifeste par
entrée) :

- **`/workspace` du relay** — c'est la racine de travail où tourne le code
  du cabinet (scripts, exports locaux, fichiers de travail du relay lié à
  la conversation/agent), donc la première chose qui doit être couverte :
  sans elle, tout ce qui n'est pas passé par le filestore conversation ou
  la KG (un export ponctuel, un fichier généré par un agent puis laissé
  sur le relay) n'est backupé nulle part. `task_def:incrementalBackup`
  parcourt ce chemin exactement comme n'importe quelle autre racine relay
  (section 16.2, point 1) — aucune capacité nouvelle à construire pour ça,
  juste s'assurer que `/workspace` fait partie de la config par défaut du
  flow plutôt que d'être un chemin à ajouter manuellement après coup.
- le filestore par conversation (pièces du dossier, section 2),
- un export de la Knowledge Graph/memory scopée aux dossiers (l'historique
  reconstruit, section 4.1),
- l'index RAG interne au cabinet s'il est auto-hébergé (section 14, point
  3) — jamais le contenu du MCP public justicelibre.org, qui n'a pas besoin
  d'être sauvegardé puisqu'il est déjà republié en source ouverte.

Point d'attention propre à `/workspace` : contrairement au filestore
conversation (déjà scopé à un dossier), un `/workspace` de relay peut
contenir du bruit (venv, node_modules, caches de build) qui n'a aucune
valeur à sauvegarder et gonflerait le volume/coût de stockage pour rien.
La configuration par défaut du flow doit donc inclure une liste
d'exclusion (`.git`, `node_modules`, `__pycache__`, `.venv`, répertoires de
cache connus) plutôt que d'aspirer tout le répertoire sans filtre — le
manifeste (section 16.2) est le bon endroit pour appliquer ce filtre avant
même de calculer les hash.

Ce ciblage reste une configuration du flow générique (plusieurs racines,
filtres d'exclusion), pas un fork spécifique au métier avocat — cohérent
avec la séparation de la section 16.3.

## 17. Workflows additionnels

Au-delà des trois flows détaillés en section 13 et du backup (section 16),
quatre workflows supplémentaires valent d'être cadrés dès maintenant — même
non retenus pour le MVP (section 8), ils clarifient où s'arrête le
périmètre :

**`flow:conflict-check`** — au moment de `flow:new-matter-intake` (section
13), recherche transversale automatique (KG + base structurée, section 20)
pour détecter si une partie du nouveau dossier apparaît déjà côté adverse
dans un autre dossier du cabinet. Alerte à vérifier par l'avocate avant
ouverture définitive — jamais un blocage automatique : la décision de
conflit d'intérêt reste un jugement professionnel, pas une règle mécanique
(cohérent avec le garde-fou §7).

**`flow:hearing-prep`** — à J-2/J-1 d'une audience identifiée par une
entité délai de type "audience", assemble automatiquement un brief
consultable (pièces du dossier, chronologie, dernières conclusions,
rappel des points en délibéré) — jamais généré comme document à déposer,
uniquement comme support de préparation pour l'avocate.

**`flow:weekly-digest`** — résumé hebdomadaire (email ou notification
in-app, lundi matin) : dossiers actifs, échéances à 7/14/30 jours, et point
utile que la section 13 ne couvre pas — dossiers *sans* activité depuis X
jours, pour repérer un dossier qui a glissé hors de l'attention plutôt que
de compter uniquement sur les rappels d'échéance déjà connues.

**`flow:jurisprudence-watch`** — s'appuie sur le MCP justicelibre.org
(section 14) : à partir de mots-clés/articles suivis par le cabinet
(déclarés par dossier ou globalement), poll périodique de nouvelles
décisions correspondantes. Notifie sans jamais résumer sans citer la
source exacte (garde-fou §7.1) — un simple lien + référence de la décision
suffit, la synthèse reste à la demande explicite de l'avocate.

**`flow:document-assembly`** — formalise la rédaction assistée déjà décrite
en section 4.7 comme un flow réutilisable (template + variables du
dossier) plutôt qu'une capacité ad hoc du chat, pour qu'il soit
déclenchable depuis l'écran dossier (section 5, actions rapides) de façon
prévisible et testable indépendamment du modèle utilisé.

Garde-fou commun aux cinq : jamais d'envoi ni de décision automatique,
toujours une citation de source quand le workflow touche du contenu
juridique, toujours une validation humaine avant action irréversible — les
mêmes trois règles que la section 7, appliquées workflow par workflow.

## 18. UI complémentaire

En prolongement des trois écrans de la section 5 :

- **Écran "Aujourd'hui"** (candidat à fusionner avec l'écran 3 échéances,
  ou à en faire l'écran par défaut au lieu de la liste des dossiers) : RDV
  du jour, délais à J-1/J-7, brouillons du secrétaire en attente de
  validation, et le contenu du `flow:weekly-digest` le lundi — un seul
  endroit qui répond à "qu'est-ce qui a besoin de moi aujourd'hui".
- **Recherche transversale globale** — barre de recherche persistante
  depuis n'importe quel écran, pas seulement dans la vue dossier ;
  implémente concrètement la fonctionnalité "tous les dossiers où on
  invoque l'article X" déjà listée en section 4.1.
- **Timeline visuelle par dossier** — représentation graphique de la
  chronologie reconstruite (section 4.1) plutôt qu'une simple liste,
  directement utile en entrée du `flow:hearing-prep` ci-dessus.
- **Écran de réglages dédié au chiffrement du backup** (section 16.2bis) —
  gestion explicite de la passphrase, jamais un champ caché dans un menu
  générique ; le risque de perte de clé doit être affiché en clair au
  moment de la configuration, pas seulement documenté à part.
- **Indicateur de fraîcheur RAG** — à côté de chaque réponse "Expert"
  sourcée (section 6), afficher la date de dernière synchronisation avec
  justicelibre.org / PISTE (section 14) : rend visible *quand* la source a
  été vérifiée, pas seulement qu'elle l'a été — renforce concrètement le
  garde-fou de citation.
- **Mode "jour d'audience"** — vue allégée, gros texte, pensée mobile/
  tablette pour consulter rapidement le brief du `flow:hearing-prep` entre
  deux dossiers, sans rouvrir l'interface complète.
- **Fil d'audit par dossier** — historique d'activité consultable
  (qui/quoi/quand, y compris les suggestions de l'agent collègue et les
  brouillons secrétaire) : nécessaire si un dossier est un jour contesté
  ("l'IA a-t-elle halluciné ici, et quand") — traçabilité, pas juste
  fonctionnalité de confort.

## 19. Base de données légère sur le relay

Question posée directement : la Knowledge Graph (triples sujet/prédicat/
objet, section 2) convient aux faits souples et aux relations, mais des
requêtes comme "tous les délais entre J et J+7 triés par urgence" ou "tous
les dossiers en statut X d'un type d'affaire donné" sont plus naturelles en
SQL qu'en traversée de graphe — la KG n'est pas le bon outil pour tout.

**Rien à construire côté plateforme : SQLite tourne déjà sur le relay sans
nouvelle capacité.** `task_def:executeSQL`/`putSQL`
(`tasks/data/execute_sql.py`) acceptent soit un service `dbConnectionPool`
(SQLite ou PostgreSQL), soit un simple paramètre `db_path` en fallback
direct — et comme les tâches de flow s'exécutent sur un `relay` explicite
(paramètre requis par tâche, cf. PFP_DEVELOPER_GUIDE), pointer ce `db_path`
vers un fichier relay-local (par exemple `/workspace/legal.db` — cohérent
avec la racine de backup ajoutée en section 16.4) donne une base SQLite
embarquée, locale au poste du cabinet, sans service serveur additionnel à
héberger ni port réseau à ouvrir.

**Ce que cette base structurée devrait porter** (complémentaire à la KG,
pas un remplacement) :

- Table `dossiers` (id, client, type_affaire, statut, date_ouverture) —
  alimente directement l'écran 1 (liste des dossiers, section 5) par un
  simple `SELECT ... ORDER BY prochaine_echeance` au lieu d'une traversée
  de graphe à chaque affichage.
- Table `delais` (id, dossier_id, type_acte, date_declenchement,
  duree_legale, date_butoir, statut, confirme_par_avocate) — `flow:
  deadline-watch` (section 13) lit/écrit ici plutôt que de scanner la KG
  entité par entité ; des colonnes indexées (`date_butoir`, `statut`)
  rendent le scan quotidien un `SELECT` trivial.
- Table `pieces` (id, dossier_id, chemin_filestore, nom, date_ajout) —
  miroir léger du filestore pour permettre un filtrage/tri SQL rapide sans
  lister le filesystem à chaque requête.
- La KG reste la source pour les faits qualitatifs et relationnels (angle
  transversal du collègue, historique reconstruit) — partage des rôles
  cohérent : SQLite pour interroger/filtrer/trier des entités structurées,
  KG pour les relations et le raisonnement.

**Cohérence avec le backup (section 16)** : ce fichier SQLite doit être une
des racines couvertes par `task_def:incrementalBackup` (fichier unique,
diff/hash trivial) et chiffré au repos comme le reste une fois transféré
(section 16.2bis). Point d'attention propre au format : activer le mode WAL
et faire un checkpoint avant chaque passage de backup (ou un verrou
applicatif court pendant le hash), pour ne jamais calculer un manifeste sur
un fichier en cours d'écriture.

**Alternative écartée** : un vrai serveur PostgreSQL au niveau du cabinet —
apporte de la robustesse concurrente mais réintroduit un service à
héberger/sécuriser/sauvegarder séparément, alors que le volume réel d'un
cabinet boutique/solo (des dizaines à quelques centaines de dossiers) ne
justifie pas cette complexité. SQLite embarqué reste cohérent avec le
positionnement self-hosted "léger" du reste du produit — et
`dbConnectionPool` garde la porte ouverte vers Postgres plus tard, pour un
cabinet plus gros, sans changer le SQL des tâches elles-mêmes.

## 20. Templating de documents — lettres types, contenu éditable

Besoin exprimé directement : un système de templating pour les documents
(courrier type, mise en demeure, accusé de réception, convocation de
RDV...) dont le contenu reste éditable par l'utilisateur, pas figé dans le
package. Ce dernier point est le vrai sujet — un template en dur dans le
.pfp signé serait immuable à l'usage, alors que chaque cabinet personnalise
ses lettres types (en-tête, formules, mentions obligatoires spécifiques).

#### 20.1 Séparation contenu par défaut / contenu éditable

Même logique que la section 16.3 (ne pas coupler ce qui doit rester
générique/éditable à ce qui est signé et versionné) : les templates par
défaut sont livrés dans le `.pfp`, mais **copiés vers un stockage
éditable propre au cabinet à l'installation**, jamais exécutés directement
depuis le contenu signé du package. Ça réutilise une règle déjà actée par
`/pfp update` (docs/PFP_PACKAGES.md §215) : une ressource localement
modifiée après install n'est plus écrasée par une mise à jour sauf
`--force` explicite — appliquer ce même principe aux templates évite
qu'une mise à jour du package avocat n'efface silencieusement la lettre
type que le cabinet a réécrite entièrement.

#### 20.2 Placeholders — réutiliser la syntaxe existante, pas en inventer une

PawFlow a déjà un langage d'expression `${...}` utilisé partout (paramètres
de flow, références de secrets, `pfp.call_tool`/`pfp.call_service`). Les
templates de documents devraient réutiliser cette même syntaxe pour les
variables du dossier plutôt qu'inventer un second langage de template à
apprendre pour le cabinet : `${dossier.client}`, `${dossier.type_affaire}`,
`${delai.date_butoir}`, etc., résolus depuis la base structurée (section
19, tables `dossiers`/`delais`) et la Knowledge Graph.

**Variable manquante = erreur visible, jamais une valeur inventée** — si
`${dossier.numero_rg}` n'existe pas encore pour ce dossier, le rendu doit
laisser un marqueur explicite ("[numero_rg manquant]") plutôt que de
laisser le modèle deviner une valeur plausible : cohérent avec le
garde-fou §7 (aucune affirmation non vérifiée présentée comme un fait),
appliqué ici aux données du dossier et pas seulement au droit.

#### 20.3 Tâche et rendu

**`task_def:renderTemplate`** — prend un `template_id` + un `dossier_id`,
résout les variables, produit un brouillon. Toujours un brouillon,
jamais un envoi ni un dépôt — le garde-fou §7.2 ("aucun envoi automatique
sans validation humaine") s'applique ici de la même façon qu'en section
4.7/12 (agent secrétaire). Format de sortie : markdown/texte par défaut,
export `.docx` optionnel — `python-docx` est déjà une dépendance du projet
(pyproject.toml, section "Document conversion"), donc générer un `.docx`
à partir du texte rendu ne demande pas de nouvelle dépendance.

**Aperçu fusionné en direct** — l'écran d'édition de template (section
20.4) doit pouvoir prévisualiser le rendu avec un dossier réel ou un
dossier d'exemple factice, pour que le cabinet voie immédiatement l'effet
d'une modification de formule sans avoir à générer un brouillon complet à
chaque essai.

#### 20.4 UI — bibliothèque de templates

Écran dédié (en prolongement de la section 18), pas un simple champ texte
noyé dans les réglages :

- Liste des templates disponibles par catégorie (courrier, mise en
  demeure, accusé de réception, convocation RDV), avec indication visuelle
  "par défaut" vs "personnalisé par le cabinet".
- Éditeur markdown/texte avec les placeholders `${...}` visuellement
  distincts (même logique que la distinction texte sourcé / brouillon de
  la section 6), et la prévisualisation fusionnée de la section 20.3
  affichée côte à côte.
- Historique des modifications par template (qui/quand, cf. le fil
  d'audit de la section 18) avec possibilité de revenir à une version
  antérieure — utile si une modification introduit une erreur dans une
  mention obligatoire.
- Action rapide "générer depuis ce template" directement depuis l'écran
  dossier (section 5), pas seulement depuis la bibliothèque.

#### 20.5 Packaging

Même raisonnement qu'en section 16.3 : le templating de documents n'est
pas spécifique au droit — un cabinet-conseil ou toute autre profession
libérale en a le même besoin. Candidat naturel à un quatrième package
générique, `platform.doc-templates` (task_def `renderTemplate`, stockage
éditable des templates, ui_extension d'édition), installable indépendamment
et simplement déclaré comme dépendance `.pfp` (docs/PFP_PACKAGES.md
§dependencies) par `firm.legal-assistant`, qui apporte lui les templates
juridiques par défaut (mise en demeure, accusé de réception) comme contenu
de départ plutôt que comme capacité propre.

#### 20.6 Import d'un .docx/.pdf existant comme point de départ

Demande explicite : l'utilisateur doit pouvoir uploader un exemple de
document existant (.docx ou .pdf) qui devient le point de départ d'un
template, plutôt que de repartir d'une page blanche dans l'éditeur.

**Rien à ajouter côté extraction : le pipeline existe déjà.**
`tasks/ai/agent_context.py` sait déjà convertir un .docx (`python-docx`) ou
un .pdf (`PyPDF2`/`pdfminer`, plus `markitdown[pdf,docx,...]` en
complément) en texte/markdown pour l'ingestion de pièces jointes — c'est
exactement la même extraction qu'il faut pour transformer un document
exemple en template éditable, sans nouvelle dépendance.

**Deux chemins d'import, pas un seul, parce que le besoin diffère selon la
source :**

1. **Import "contenu" (texte/markdown)** — pour un .pdf, ou un .docx dont
   seul le texte compte. Extraction via le pipeline existant, résultat
   déposé comme brouillon de template dans l'éditeur (section 20.4). Le
   cabinet marque ensuite lui-même les emplacements variables en tapant
   les placeholders `${dossier...}` (section 20.2) à la main dans le texte
   extrait — l'import fait gagner la rédaction de base, pas la détection
   des variables.
2. **Import "mise en forme" (.docx natif, formatage préservé)** — pour un
   courrier avec en-tête/logo/mise en page du cabinet, où repasser par du
   markdown perdrait la charte graphique. Le .docx original est conservé
   tel quel comme fichier template ; le cabinet insère les placeholders
   `${...}` directement dans le texte du document (dans Word, ou dans un
   éditeur limité côté PawFlow) et `renderTemplate` (section 20.3)
   substitue ces runs de texte via `python-docx` au moment du rendu,
   plutôt que de reconstruire le document depuis du markdown — la mise en
   page d'origine (marges, en-tête, style) reste intacte dans le
   brouillon final. C'est le chemin à privilégier pour la correspondance
   réelle du cabinet (mise en demeure, courrier à en-tête).

Un .pdf ne devrait jamais être la cible de rendu — un PDF n'a pas de
structure éditable fiable pour une substitution in-place. Un .pdf importé
nourrit uniquement le chemin 1 (extraction de contenu), jamais le chemin 2.

**Suggestion assistée, jamais automatique** — une passe optionnelle de
l'agent collègue (section 12) pourrait proposer des emplacements probables
de variables (noms propres, dates, montants détectés dans le texte
importé) comme suggestions surlignées à valider une par une, jamais
insérées directement : même principe que le reste du rôle collègue
("propose, ne décide jamais") et cohérent avec le garde-fou de la section
20.2 — deviner qu'un nom est une variable n'est pas différent de deviner
une valeur de variable, les deux restent une proposition à valider par le
cabinet, jamais un fait appliqué.

## 21. Fine-tuning d'un modèle local (Qwen3.6-35B-A3B ou équivalent)

Demande explicite : évaluer le fine-tuning d'un modèle local type
Qwen3.6-35B-A3B (MoE, generation la plus récente au moment de la rédaction
de la famille Qwen3, succédant à Qwen3-30B-A3B/Qwen3-235B-A22B déjà publiés
en poids ouverts — les specs exactes de la version 3.6 n'ont pas de source
publique claire trouvée en recherche, à confirmer avant tout engagement
matériel). Recherche faite sur l'écosystème d'outillage (Unsloth,
LLaMA-Factory, PEFT/QLoRA), pas de dépôt spécifique 3.6 identifié — la
méthode ci-dessous s'applique de la même façon quelle que soit la version
exacte disponible au moment de l'implémentation.

#### 21.1 Où le fine-tuning aide réellement — et où il ne doit jamais servir

Point de méthode qui découle directement du garde-fou §7 et de la section
11 (routage par rôle) : le fine-tuning est le bon outil pour le **style**,
jamais pour les **faits**.

- **Utile** — cohérence de ton et de formule ("voix" propre au cabinet dans
  les brouillons secrétaire/collègue), familiarité avec les conventions de
  rédaction juridique française et les formats de template déjà en place
  (section 20) — réduit le prompt engineering nécessaire pour obtenir un
  brouillon dans le style attendu du premier coup.
- **À proscrire** — injecter du contenu juridique (articles, jurisprudence,
  délais) dans les poids du modèle par fine-tuning. C'est exactement
  l'inverse de l'architecture retenue en section 14 : un fait figé dans des
  poids ne peut pas porter de date de version comme `get_law_article(code,
  num, date)`, ne peut pas être mis à jour sans réentraîner, et peut
  halluciner une version obsolète avec la même confiance apparente qu'une
  citation exacte. Le RAG (justicelibre.org + index interne) reste l'unique
  source de vérité juridique ; le fine-tuning ne doit jamais s'y substituer,
  seulement améliorer la forme du texte autour des passages retrouvés.

#### 21.2 Méthode — LoRA/QLoRA, pas un fine-tuning complet

Réentraîner l'intégralité des poids d'un modèle 35B (même MoE à ~3B de
paramètres actifs) est hors de portée du budget matériel d'un cabinet
boutique/solo. La voie réaliste est un fine-tuning à paramètres efficients
(LoRA, ou QLoRA en 4-bit pour réduire encore la mémoire), via un outillage
existant plutôt qu'un pipeline à construire — Unsloth ou LLaMA-Factory
supportent déjà Qwen3 en LoRA/QLoRA (à vérifier explicitement pour la
variante MoE A3B précise disponible, le support LoRA sur architecture MoE
est plus délicat qu'en dense : cibler l'attention et les couches partagées
plutôt que tenter du LoRA par expert individuellement, sauf si l'outil le
gère nativement).

Ordre de grandeur matériel (estimation non vérifiée indépendamment, à
tester avant tout achat) : un QLoRA en 4-bit sur un modèle ~30-35B devrait
tenir sur un seul GPU 24 Go de VRAM haut de gamme grand public/prosumer,
dans la même gamme que ce qu'Unsloth documente pour Qwen3-30B-A3B — mais
une architecture MoE garde tous les experts en mémoire même si peu sont
actifs par token, donc l'empreinte mémoire réelle peut être plus proche
d'un dense ~30B que d'un dense ~3B. À valider par un essai réel avant de
cadrer un budget matériel définitif.

Sortie utilisable ensuite comme un `service_provider` LLM auto-hébergé via
Ollama/vLLM — exactement l'option 1 du menu confidentialité de la section
11 (aucune donnée ne sort jamais), ce qui fait du fine-tuning local un
complément naturel de cette option plutôt qu'un chantier séparé.

#### 21.3 Données d'entraînement — le vrai chantier, pas le calcul GPU

Ce que le fine-tuning demande vraiment, c'est des paires
(contexte dossier → brouillon dans le style du cabinet), pas du texte brut
:

- **Source** : la correspondance passée du cabinet (courriers, mises en
  demeure, conclusions) — le même corpus déjà identifié comme cible du
  backup interne (section 16.4) et de l'import de templates (section
  20.6). Reformater en paires d'instruction ("contexte factice/anonymisé →
  texte final rédigé par le cabinet") plutôt que d'entraîner sur du texte
  brut non structuré, qui donnerait un modèle qui complète du texte plutôt
  qu'un modèle qui rédige à partir d'un contexte de dossier.
- **Volume réaliste** : un fine-tuning de style efficace se construit
  typiquement avec quelques centaines à quelques milliers de paires bien
  choisies, pas des millions d'exemples — un cabinet boutique/solo avec des
  années d'archives numérisées en a plausiblement assez, si elles sont
  correctement nettoyées et formatées. C'est cette mise en forme qui est
  l'effort réel, pas l'entraînement GPU lui-même.
- **Anonymisation stricte avant tout entraînement, même 100% local** — un
  point technique distinct de la confidentialité "ne rien envoyer à un
  tiers" (section 11) : un modèle fine-tuné peut mémoriser et régurgiter
  verbatim des fragments de ses données d'entraînement sous certaines
  requêtes (risque documenté de mémorisation/fuite par inférence). Un
  modèle entraîné sur des dossiers clients non anonymisés reste un risque
  de fuite même s'il ne quitte jamais le matériel du cabinet — remplacer
  noms/montants/dates identifiantes par des marqueurs neutres avant
  d'utiliser un dossier réel comme exemple d'entraînement, pas seulement
  avant de l'envoyer à une API externe.

#### 21.4 Où le cadrer dans la feuille de route

Cohérent avec l'ordre de priorité de la section 8 : le fine-tuning est un
chantier V2+, pas MVP. Deux raisons qui se renforcent :

1. Le RAG (section 14) et le garde-fou de citation (`skill:legal-citations`)
   couvrent déjà l'essentiel de la valeur différenciante — le fine-tuning
   n'ajoute qu'un polish de style, pas une capacité manquante.
2. Le problème du "pas encore assez de données" se résout de lui-même en
   V2 : une fois le MVP en usage réel, les brouillons générés et corrigés
   par l'avocate (section 4.7) deviennent eux-mêmes le corpus
  contexte→correction idéal pour un futur fine-tuning, alors qu'il
   faudrait aujourd'hui reconstruire ces paires depuis des archives brutes.

Point de sécurité qui rend l'expérimentation peu risquée le moment venu :
le garde-fou "toujours un brouillon, jamais un envoi automatique" (section
4.7/7) s'applique à un modèle fine-tuné exactement comme au modèle de base
— un mauvais fine-tuning produit un brouillon de moins bonne qualité, pas
une action non supervisée. Ça permet de tester un modèle local fine-tuné en
production contrôlée sans élargir la surface de risque déjà cadrée par les
garde-fous existants.

## 22. Conclusion

PawFlow est un bon socle : le gros de l'infrastructure (conversations
persistantes, memory/KG, flows, auth email, et maintenant calendrier) existe
déjà. L'effort réel est l'assemblage métier et surtout la discipline sur les
garde-fous — un cabinet d'avocat ne pardonne pas les approximations que
d'autres domaines tolèrent. Le point qui bloquait la livraison en .pfp telle
que demandée n'était pas métier mais plateforme : l'URL dédiée exigeait un
type d'objet .pfp qui n'existait pas encore (section 9.2) — livré depuis en
1.0.0-beta.30.

Le backup incrémental (section 16) suit le même principe : les services de
destination (`googleDrive`, `rcloneFilesystem`) existent déjà côté cœur
PawFlow, seule la tâche de diff/manifeste reste à écrire, et elle doit
l'être comme package `.pfp` générique et indépendant plutôt que comme
fonctionnalité du package avocat — c'est ce qui permet de composer un
déploiement à la carte : install standard + `.pfp` avocat + `.pfp` backup,
chacun installable, mis à jour et désinstallé sans toucher aux deux autres.

Les sections 17 à 19 élargissent le périmètre plutôt que de le refermer :
cinq workflows supplémentaires (conflit d'intérêt, préparation d'audience,
digest hebdomadaire, veille jurisprudentielle, assemblage de courrier), des
écrans complémentaires cohérents avec les trois déjà cadrés en section 5,
et une base structurée légère (SQLite, déjà supportée nativement sur le
relay via `executeSQL`/`dbConnectionPool`) pour les requêtes que la
Knowledge Graph ne sert pas naturellement. Aucune de ces additions ne
demande de nouvelle capacité plateforme — le socle déjà en place absorbe
tout, ce qui confirme le diagnostic de départ : l'effort restant est
l'assemblage et la discipline, pas l'infrastructure.
