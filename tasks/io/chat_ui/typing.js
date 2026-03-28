const FUN_VERBS = [
  // Tech / Dev
  'Refactoring','Compiling','Debugging','Deploying','Optimizing','Linting','Minifying',
  'Transpiling','Dockerizing','Kuberneting','Microservicing','Rebasing','Merging',
  'Cherry-picking','Hotfixing','Monkey-patching','Sharding','Indexing','Caching',
  'Serializing','Deserializing','Tokenizing','Lexing','Parsing','Hashing','Encrypting',
  'Decrypting','Handshaking','Pipelining','Webhooking','Load-balancing','Auto-scaling',
  'Containerizing','Orchestrating','Provisioning','Terraforming','Ansibilizing',
  'GitOpsing','CI/CDing','Blue-greening','Canary-deploying','Feature-flagging',
  'A/B-testing','Stress-testing','Fuzz-testing','Benchmarking','Profiling',
  'Flame-graphing','Heap-dumping','Thread-pooling','Garbage-collecting','JITting',
  'AOT-compiling','Tree-shaking','Code-splitting','Lazy-loading','Prefetching',
  'Service-meshing','API-gatewaying','Rate-limiting','Circuit-breaking','Bulkheading',
  'Backpressuring','Dead-lettering','Event-sourcing','CQRS-ing','Saga-patterning',
  // Science
  'Hypothesizing','Experimenting','Calibrating','Quantifying','Synthesizing',
  'Centrifuging','Titrating','Distilling','Crystallizing','Polymerizing',
  'Sequencing','Splicing','Cloning','Mutating','Evolving','Spectrographing',
  'Electron-microscoping','Carbon-dating','Peer-reviewing','Replicating',
  'Correlating','Extrapolating','Interpolating','Normalizing','Standardizing',
  'Ionizing','Magnetizing','Polarizing','Oscillating','Resonating',
  'Diffracting','Refracting','Superposing','Entangling','Tunneling',
  'Annihilating','Fissioning','Fusioning','Plasma-confining','Supercooling',
  // Space
  'Launching','Orbiting','Docking','Spacewalking','Terraforming','Warp-driving',
  'Hyperjumping','Lightspeed-calculating','Asteroid-mining','Stargazing',
  'Nebula-surfing','Black-holing','Graviton-emitting','Solar-sailing',
  'Cryo-sleeping','Planet-scanning','Exoplanet-hunting','Comet-chasing',
  'Satellite-deploying','Moon-landing','Mars-colonizing','Ring-surfing',
  'Supernova-watching','Pulsar-timing','Quasar-mapping','Dark-mattering',
  'Cosmic-raying','Redshift-measuring','Singularity-approaching','Dyson-sphering',
  // Gaming
  'Respawning','Looting','Crafting','Speed-running','Combo-breaking',
  'Boss-fighting','Level-grinding','Rage-quitting','Tea-bagging','No-scoping',
  'Wall-running','Double-jumping','Rocket-jumping','Bunny-hopping','Strafing',
  'Camping','Ganking','Kiting','Aggro-pulling','Mana-regenerating',
  'Buff-stacking','Debuffing','Critical-hitting','Parrying','Dodge-rolling',
  'Inventory-managing','Quest-logging','Achievement-unlocking','Leaderboarding',
  'Speedhacking','Glitch-exploiting','Sequence-breaking','Any-percenting',
  'Frame-perfecting','Pixel-walking','Clipping','Noclipping','God-moding',
  // Cuisine
  'Sautéing','Flambéing','Caramelizing','Blanching','Braising','Julienning',
  'Deglazing','Reducing','Emulsifying','Fermenting','Proofing','Kneading',
  'Tempering','Sous-viding','Smoking','Curing','Pickling','Brining',
  'Marinating','Basting','Glazing','Torching','Dehydrating','Infusing',
  'Zesting','Chiffonading','Brunoise-cutting','Folding','Whipping',
  'Meringue-piping','Ganache-pouring','Crème-brûlée-ing','Sourdough-feeding',
  'Umami-boosting','Mise-en-placing','Knife-sharpening','Wok-haying',
  // Animals
  'Catifying','Doggoing','Penguin-waddling','Chameleon-blending','Dolphin-clicking',
  'Owl-hooting','Squirrel-stashing','Bee-pollinating','Spider-webbing',
  'Flamingo-posing','Sloth-hanging','Cheetah-sprinting','Whale-singing',
  'Parrot-mimicking','Octopus-camouflaging','Peacock-displaying','Beaver-damming',
  'Ant-marching','Butterfly-morphing','Gecko-climbing','Otter-floating',
  'Pangolin-curling','Axolotl-regenerating','Tardigrade-surviving',
  'Narwhal-jousting','Platypus-confusing','Capybara-chilling','Red-panda-ing',
  // Music
  'Beatboxing','Harmonizing','Riffing','Improvising','Crescendo-ing',
  'Syncopating','Arpeggiating','Tremolo-picking','Shredding','Djent-ing',
  'Dubstepping','Drum-rolling','Bass-dropping','Vinyl-scratching',
  'Auto-tuning','Looping','Sampling','Remixing','Mastering','EQ-ing',
  'Side-chaining','Reverb-drenching','Pitch-bending','Vocoding',
  'Theremin-waving','Yodeling','Beatmatching','Crossfading',
  // Magic / Fantasy
  'Enchanting','Conjuring','Transmuting','Summoning','Banishing','Scrying',
  'Wand-waving','Potion-brewing','Spell-casting','Rune-carving','Hexing',
  'Shapeshifting','Teleporting','Levitating','Astral-projecting',
  'Crystal-gazing','Alchemy-ing','Elixir-mixing','Grimoire-reading',
  'Familiar-bonding','Mana-channeling','Portal-opening','Illusion-weaving',
  'Necromancy-ing','Divination-ing','Abjuring','Evoking','Invoking',
  // Sports
  'Slam-dunking','Bicep-curling','Parkour-ing','Bouldering','Skateboarding',
  'Snowboarding','Surfing','Hang-gliding','Base-jumping','Free-running',
  'Cartwheeling','Backflipping','Pole-vaulting','Javelin-throwing',
  'Hurdle-clearing','Sprint-finishing','Marathon-pacing','Triathlon-ing',
  'CrossFit-ing','Deadlifting','Kettlebell-swinging','Yoga-posing',
  // Absurd / Inventés
  'Combobulating','Discombobulating','Recombobulating','Confuzzling',
  'Flibbergibbeting','Lollygagging','Dillydallying','Shillyshallying',
  'Skedaddling','Bamboozling','Cattywampusing','Gobsmacking','Wibble-wobbling',
  'Fluffernuttying','Kerfuffling','Hullabaloo-ing','Rigmarole-ing',
  'Bumblebee-ing','Malarkey-detecting','Shenanigan-foiling','Tomfoolery-ing',
  'Razzle-dazzling','Higgledy-piggling','Topsy-turvying','Wishy-washying',
  'Namby-pambying','Mumbo-jumboing','Hanky-pankying','Hocus-pocusing',
  'Abracadabra-ing','Supercalifragilisting','Whatchamacalliting',
  'Thingamajiggling','Doohickey-ing','Gizmo-fiddling','Widget-twiddling',
  'Doodad-adjusting','Contraption-ing','Rigamarole-ing','Brouhaha-ing',
  'Snafu-resolving','Fubar-unfubaring','Defenestrating','Discountenance-ing',
  'Flibberflabbering','Jibberjabbering','Gobbledygooking','Bibblebopping',
  'Rumpelstiltskin-ing','Serendipity-ing','Onomatopoeia-ing',
  'Antidisestablishmentarian-izing','Floccinaucinihilipilificating',
  'Pneumonoultramicroscopicsilico-ing','Hippopotomonstrosesquipedalian-ing',
  'Llanfairpwllgwyngyll-ing','Superdupering','Mega-ultra-ing',
  'Hyper-turbo-charging','Quantum-fluctuating','Nano-assembling',
  'Cyber-synergizing','Techno-babbling','Retro-encabulating','Turbo-encabulating',
  'Reverse-polarity-ing','Flux-capacitoring','Dilithium-crystaling',
  'Unobtainium-mining','Handwavium-applying','Plotholeum-patching',
  'Deux-ex-machina-ing','McGuffin-locating','Plot-armoring','Mary-Sue-ing',
  'Timey-wimey-ing','Wibbly-wobbly-ing','Ding-donging','Zigzagging',
  'Roly-polying','Teeter-tottering','Pitter-pattering','Clip-clopping',
  'Tick-tocking','Flip-flopping','Ping-ponging','Zig-zagging',
  'Shilly-shallying','Willy-nillying','Hokey-pokeying','Okey-dokeying',
  'Artsy-fartsying','Boogie-woogieing','Heebie-jeebieing','Lovey-doveying',
  'Itsy-bitsying','Teeny-weenying','Oopsie-daisy-ing','Easy-peasy-ing',
  // Pop culture
  'Jedi-mind-tricking','Force-pushing','Lightsaber-dueling','Kessel-running',
  'Pokémon-catching','Pikachu-thunderbolting','Hadouken-ing','Kamehameha-ing',
  'Falcon-punching','Shoryuken-ing','Fatality-performing','Mortal-Kombat-ing',
  'Mario-jumping','Sonic-spinning','Zelda-puzzle-solving','Master-sword-pulling',
  'Triforce-assembling','Portal-thinking','Cake-lying','Weighted-cube-loving',
  'Skyrim-sweetrolling','Arrow-to-the-kneeing','Minecraft-crafting',
  'Creeper-avoiding','Enderman-staring','Nether-portaling','Among-Us-venting',
  'Impostor-detecting','Rickrolling','Gandalf-passing','Hobbit-walking',
  'Precious-hunting','Infinity-stone-snapping','Vibranium-forging',
  'Wakanda-forevering','Avengers-assembling','Bat-signaling','Kryptonite-avoiding',
  'Web-slinging','Groot-growing','Baby-Yoda-sipping','Mandalorian-waying',
  'Allons-y-ing','Exterminating','Regenerating','TARDIS-materializing',
  // Philosophy / Abstract
  'Contemplating','Ruminating','Philosophizing','Pontificating','Cogitating',
  'Deliberating','Meditating','Introspecting','Existential-crisis-ing',
  'Nihilism-overcoming','Absurdism-embracing','Trolley-problem-solving',
  'Ship-of-Theseus-ing','Brain-in-a-vat-ing','Cogito-ergo-summing',
  'Categorical-imperative-ing','Virtue-ethic-ing','Utilitarian-calculating',
  'Dialectic-synthesizing','Phenomenology-reducing','Epistemology-ing',
  'Ontology-questioning','Hermeneutic-circling','Deconstructing',
  // Weather / Nature
  'Photosynthesizing','Cloud-seeding','Lightning-conducting','Tornado-chasing',
  'Tsunami-surfing','Earthquake-shaking','Volcano-erupting','Geyser-timing',
  'Aurora-borealis-ing','Tidal-waving','Monsoon-weathering','Blizzard-braving',
  'Rainbow-chasing','Dewdrop-collecting','Snowflake-crystallizing',
  'Tectonic-shifting','Continental-drifting','Erosion-sculpting',
  // Math
  'Differentiating','Integrating','Fourier-transforming','Eigenvalue-decomposing',
  'Matrix-multiplying','Gradient-descending','Backpropagating','Bayesian-updating',
  'Monte-Carlo-simulating','Regression-fitting','Clustering','Dimensionality-reducing',
  'Fibonacci-spiraling','Pi-calculating','Prime-sieving','Mandelbrot-zooming',
  'Fractal-iterating','Topology-bending','Riemann-hypothesizing',
  'P-vs-NP-wondering','Halting-problem-halting','Turing-completing',
  // Art / Creative
  'Watercoloring','Oil-painting','Sculpting','Chiseling','Pottery-wheeling',
  'Glaze-firing','Origami-folding','Calligraphy-ing','Cross-hatching',
  'Stippling','Impasto-layering','Glazing','Wet-on-wetting','Bob-Ross-ing',
  'Happy-little-treeing','Beat-the-devil-out-of-iting','Pixel-arting',
  'Voxel-modeling','UV-unwrapping','Rigging','Mocap-performing',
  'Rotoscoping','Compositing','Color-grading','Storyboarding',
  // Office / Corporate
  'Synergizing','Leveraging','Circling-back','Touching-base','Ping-ing',
  'Action-iteming','Deliverable-delivering','KPI-tracking','OKR-setting',
  'Standup-standing','Retro-specting','Sprint-planning','Backlog-grooming',
  'Story-pointing','Velocity-calculating','Burn-down-charting','Kanban-boarding',
  'Jira-ticketing','Confluence-documenting','Slack-threading','Zoom-fatiguing',
  'Calendar-tetris-ing','Meeting-about-meetings-ing','Email-cc-ing',
  'Reply-all-apologizing','Out-of-office-autoreplying','TPS-reporting',
  'Cover-sheet-attaching','Paradigm-shifting','Moving-the-needle',
  'Boiling-the-ocean','Low-hanging-fruiting','Value-adding',
  // AI / ML
  'Neural-networking','Deep-learning','Attention-paying','Transformer-attending',
  'Tokenizing','Embedding','Fine-tuning','RLHF-ing','Hallucination-avoiding',
  'Prompt-engineering','Chain-of-thoughting','Few-shot-learning',
  'Zero-shot-guessing','Gradient-clipping','Dropout-regularizing',
  'Batch-normalizing','Softmax-squishing','ReLU-activating',
  'Convolution-sliding','Pooling','Upsampling','GAN-generating',
  'Discriminator-fooling','Diffusion-denoising','LoRA-adapting',
  'Quantizing','Distilling','Pruning','Knowledge-graphing',
  'Retrieval-augmenting','Vector-searching','Cosine-similaritying',
  'Attention-is-all-you-needing','GPT-ing','BERT-masking','LLM-inferring',
  // Construction / Craft
  'Hammering','Nailing','Sawing','Sanding','Varnishing','Welding',
  'Soldering','Riveting','Plumbing','Wiring','Drywalling','Tiling',
  'Grouting','Caulking','Spackling','Priming','Basecoating','Topcoating',
  'Dovetail-joining','Mortise-tenoning','Lathe-turning','Bandsaw-cutting',
  // Dance
  'Moonwalking','Breakdancing','Waltzing','Tangoing','Salsa-ing',
  'Cha-cha-ing','Foxtrotting','Robot-dancing','Macarena-ing',
  'Flossing','Dabbing','Nae-nae-ing','Electric-sliding',
  'Riverdancing','Pirouetting','Voguing','Krumping','Tutting',
  // Household
  'Vacuum-cleaning','Dish-washing','Laundry-folding','Dust-bunnying',
  'Decluttering','Marie-Kondo-ing','Sparking-joy','Sock-pairing',
  'Tupperware-lid-matching','Remote-control-finding','Junk-drawer-organizing',
  'Fridge-tetris-ing','Couch-cushion-mining','Lint-rolling',
  // Internet
  'Doomscrolling','Meme-crafting','Copypasta-ing','Emoji-translating',
  'Hashtag-optimizing','Influencer-ing','Vlogging','Unboxing',
  'Click-baiting','SEO-optimizing','Cookie-accepting','CAPTCHA-solving',
  'Two-factor-authenticating','Password-resetting','Incognito-tabbing',
  'Tab-hoarding','Bookmark-organizing','Cache-clearing','Ad-blocking',
  'Dark-mode-enabling','Notification-silencing','Read-receipting',
  // Time-related
  'Procrastinating','Speedrunning','Time-traveling','Chrono-shifting',
  'Temporal-looping','Groundhog-daying','Déjà-vu-ing','Future-proofing',
  'Retro-grading','Nostalgia-tripping','Yesterday-remembering',
  'Tomorrow-planning','Deadline-approaching','Timezone-converting',
  // Emotions
  'Vibing','Manifesting','Zen-achieving','Chakra-aligning',
  'Aura-cleansing','Energy-matching','Good-vibes-only-ing',
  'Serotonin-boosting','Dopamine-hitting','Endorphin-rushing',
  'ASMR-tingling','Hygge-cozying','Wanderlust-ing',
  // Misc fun
  'Bubble-wrapping','Tetris-fitting','Rubik-cubing','Sudoku-solving',
  'Crossword-puzzling','Jenga-pulling','Domino-toppling','Rube-Goldberging',
  'Swiss-army-knifing','Duct-taping','Zip-tying','Bungee-cording',
  'MacGyver-ing','Life-hacking','Percussive-maintaining','Turning-it-off-and-on-again-ing',
  'Blowing-on-the-cartridge','Have-you-tried-restarting','Stack-overflowing',
  'Copy-pasting-from-SO','Works-on-my-machining','RTFM-ing','LGTM-ing',
  'Ship-it-ing','YOLO-deploying','Friday-deploying','Hotfix-on-prod-ing',
  'Git-blame-ing','Rubber-duck-debugging','Rage-coding','Caffeine-loading',
  'Coffee-brewing','Energy-drink-chugging','Snack-refueling','Pizza-ordering',
  'Nap-recharging','Cat-on-keyboard-handling','Tab-explosion-managing',
  'Infinite-loop-escaping','Segfault-investigating','Null-pointer-dereferencing',
  'Off-by-one-correcting','Semicolon-hunting','Bracket-matching',
  'Indentation-warring','Bikeshedding','Yak-shaving','Nerd-sniping',
  'Scope-creeping','Feature-creeping','Gold-plating','Over-engineering',
  'Premature-optimizing','Cargo-culting','Spaghetti-untangling',
  'Technical-debt-paying','Legacy-code-archeology-ing','Dependency-hell-escaping',
  'Node-modules-downloading','Left-pad-replacing','Is-it-DNS-checking',
  'Blame-the-network-ing','Firewall-blaming','Cloud-yelling-at',
  'Serverless-servering','NoSQL-not-only-SQLing','Blockchain-ing',
  'Web3-pivoting','NFT-minting','Metaverse-entering','AI-bubble-riding',
  'Buzzword-generating','Jargon-deploying','Acronym-expanding',
  'TLA-decoding','FYI-forwarding','Per-my-last-emailing',
  'New-phone-who-dis-ing','Rubber-stamping','Green-lighting'
];// ── Typing indicators ─────────────────────────────────────────────
let typingInterval = null;
const TYPING_COLORS = [
  '#a78bfa','#f472b6','#34d399','#fbbf24','#60a5fa',
  '#fb923c','#e879f9','#2dd4bf','#f87171','#a3e635',
  '#818cf8','#fb7185','#4ade80','#facc15','#38bdf8',
  '#f97316','#c084fc','#22d3ee','#ef4444','#84cc16',
];
let typingColorIdx = 0;

function randomVerb() {
  return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)];
}

function randomColor() {
  typingColorIdx = (typingColorIdx + 1) % TYPING_COLORS.length;
  return TYPING_COLORS[typingColorIdx];
}

function showTyping() {
  // If already showing, don't recreate (avoids layout thrashing)
  if (document.getElementById('typing')) return;
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'typing';
  const color = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + color + '">✻</span>'
    + '<span class="verb" style="color:' + color + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  typingInterval = setInterval(() => {
    const t = document.getElementById('typing');
    if (t) {
      const c = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
        + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideTyping() {
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  const el = document.getElementById('typing');
  if (el) el.remove();
}

let contextOpInterval = null;
function showContextOp(label) {
  contextOpInProgress = true;
  hideContextOp();
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'contextOpTyping';
  const c = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
    + '<em style="color:' + c + '">' + label + '</em> '
    + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  contextOpInterval = setInterval(() => {
    const t = document.getElementById('contextOpTyping');
    if (t) {
      const c2 = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c2 + '">✻</span>'
        + '<em style="color:' + c2 + '">' + label + '</em> '
        + '<span class="verb" style="color:' + c2 + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideContextOp() {
  if (contextOpInterval) { clearInterval(contextOpInterval); contextOpInterval = null; }
  const el = document.getElementById('contextOpTyping');
  if (el) el.remove();
}

startActiveSync();
