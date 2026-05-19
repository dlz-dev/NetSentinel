# Préparation démo — NetSentinel Streaming
**Vendredi | 20 min | Linda Wang**

---

## Partie 1 — Démo [5 min]

**Script à suivre dans l'ordre**

1. Ouvrir `http://localhost:8060` → landing page
   - *"Voici la landing page du projet — les métriques du modèle entraîné en batch : 99.3% d'accuracy, 97% de F1 macro sur 10 classes d'attaques."*

2. Cliquer "Analyse Live"
   - *"On passe au dashboard streaming. Là on voit le trafic WiFi de ma machine capturé en temps réel — exactement comme Wireshark, mais avec une classification ML sur chaque flux."*

3. Montrer le trafic bénin qui monte
   - *"Tout ce trafic en vert c'est mon WiFi — navigateur, mises à jour, DNS… le modèle le classe Benign en moins d'une seconde."*

4. Cliquer "⚡ Simuler attaque"
   - *"Je simule une attaque — le bouton injecte de vrais flux du dataset CIC-IDS-2017 dans le pipeline."*
   - Attendre la modale → *"L'alerte arrive automatiquement."*

5. Cliquer "Analyser avec Claude"
   - *"Je demande à Claude Haiku d'analyser la menace — il reçoit le type d'attaque et les métriques du modèle, et produit une analyse contextuelle."*
   - Pendant que Claude répond → montrer LangSmith : *"Là on voit le trace en direct — le prompt exact, les tokens, la latence."*

6. Cliquer sur une barre dans "Classes détectées"
   - *"Je peux drill-down directement depuis le graphique pour filtrer le feed sur une classe précise."*

7. Cliquer "📥 Rapport"
   - *"Le rapport HTML se génère avec tout — KPIs, top IPs, métriques modèle, recommandations par type d'attaque."*

---

## Partie 2 — Explication technique [5 min]

### Choix faits

**Pourquoi Kafka et pas directement des CSV ?**
Kafka découple les producteurs du consommateur : `live_capture` et `dataset_replay` publient des messages JSON dans un topic sans savoir qui lit. Le consommateur (`kafka_consumer.py`) écrit les CSV d'époques et le dashboard ne change pas. C'est le pattern producteur → broker → consommateur, exactement ce qu'on voit en production sur un vrai réseau. On utilise KRaft (Kafka sans Zookeeper) via Docker Bitnami, ce qui évite d'avoir deux services à gérer.

**Pourquoi sklearn pour le live et pas Spark MLlib ?**
nfstream génère les flux un à la fois. Démarrer une SparkSession pour classifier 1 flux = plusieurs secondes de latence JVM. Le modèle sklearn répond en millisecondes. C'est le bon outil au bon endroit.

**Pourquoi deux plages d'époques séparées ?**
Bug rencontré en développement : quand `dataset_replay` écrivait `epoch_090000`, le dashboard avançait son curseur à 90000 et excluait définitivement tout le trafic live (0–89999). Fix : deux curseurs indépendants `last_live` et `last_replay` dans le store Dash.

### Métriques

| Métrique | Valeur |
|---|---|
| Latence end-to-end | ~1 seconde (fast-tick interval) |
| Mise à jour des graphiques | ~3 secondes (slow-tick interval) |
| Taille micro-batch live | 1 flux / epoch (live_capture) |
| Taille micro-batch replay | 16 flux / epoch (dataset_replay) |
| Fréquence replay | 1 batch toutes les 2 minutes |
| Buffer feed | 500 derniers flux en mémoire |

### Limites constatées

- **IPs privées** — le dataset CIC-IDS-2017 vient d'un labo fermé, toutes les IPs sont en 192.168.x.x. Impossible de faire de la géolocalisation.
- **Faux positifs** — 3% d'erreur du modèle RF se manifeste sur le trafic WiFi réel (ex: FTP-Patator détecté sur du trafic bénin).
- **Dataset de 2017** — le modèle ne connaît que les attaques de 2017. Un vrai zero-day passerait inaperçu.
- **Pas de vrai broker** — le pattern CSV est fonctionnel en démo mais ne scale pas sur un vrai réseau d'entreprise.

---

## Partie 3 — Appréciation des technologies [5 min]

### Apache Spark + Spark MLlib
**Ce que j'ai apprécié :** La lazy evaluation est une vraie révélation — supprimer 75 colonnes sur 2.6M lignes prend 0.3ms, alors qu'un `.count()` prend 22 secondes. Une fois qu'on comprend ça, on lit le code différemment. Le Spark UI pour débugger les plans d'exécution est aussi très puissant.
**Ce qui m'a frustré :** La configuration mémoire est complexe et les erreurs JVM sont opaques. `Java heap space out of memory` pendant la CrossValidation m'a bloqué plusieurs heures.
**Est-ce que je le réutiliserais ?** Oui, dès que le volume dépasse quelques centaines de Mo. En dessous, pandas suffit largement.

### Dash / Plotly
**Ce que j'ai apprécié :** Très rapide pour prototyper un dashboard interactif en Python pur, sans toucher à React ou JavaScript. Le système de callbacks est élégant.
**Ce qui m'a frustré :** Le système de callbacks devient complexe avec beaucoup de composants dynamiques — j'ai eu des bugs liés aux pattern-matching callbacks (`dash.ALL`) qui crashaient quand aucun composant ne correspondait. Difficile à débugger.
**Est-ce que je le réutiliserais ?** Oui pour de la data viz interne ou des démos. Pas pour une application web publique.

### Kedro
**Ce que j'ai apprécié :** Le Data Catalog est vraiment bien pensé — plus de chemins hardcodés dans le code, tout est déclaré en YAML. La séparation nodes / pipeline / catalog force une architecture propre.
**Ce qui m'a frustré :** La courbe d'apprentissage est raide. La documentation est dense et les messages d'erreur pas toujours clairs au début.
**Est-ce que je le réutiliserais ?** Oui, sur tout projet data qui dure plus de 2 semaines. Sur un PoC rapide, c'est du overhead.

### MLflow + kedro-mlflow
**Ce que j'ai apprécié :** Le tracking automatique à chaque `kedro run` sans une seule ligne de code supplémentaire. Le Model Registry avec les stages Staging/Production/Archived est très propre.
**Est-ce que je le réutiliserais ?** Oui systématiquement. C'est devenu un réflexe — ne plus jamais entraîner un modèle sans le tracker.

### DVC
**Ce que j'ai apprécié :** Le concept est excellent — Git pour le code, DVC pour les données. `dvc pull` qui restaure exactement les mêmes données sur n'importe quelle machine, c'est la vraie reproductibilité.
**Ce qui m'a frustré :** La config Google Drive peut être capricieuse selon les permissions OAuth.
**Est-ce que je le réutiliserais ?** Oui dès qu'il y a des fichiers > 50MB à versionner.

### nfstream
**Ce que j'ai apprécié :** Capture le trafic réseau et calcule directement les features statistiques de flux (IAT, bytes, flags TCP…) sans avoir à parser des paquets bruts. C'est exactement ce qu'il faut pour alimenter un modèle ML.
**Ce qui m'a frustré :** Documentation limitée, quelques comportements inattendus sur Windows.
**Est-ce que je le réutiliserais ?** Oui, c'est le seul outil Python qui fait ça proprement.

### LangChain + Claude + LangSmith
**Ce que j'ai apprécié :** LangSmith qui trace chaque appel LLM automatiquement — voir le prompt exact, la réponse, les tokens et la latence en temps réel, c'est le MLflow des LLM. Claude Haiku est impressionnant pour le rapport qualité/prix/vitesse.
**Est-ce que je le réutiliserais ?** Oui. Intégrer un LLM dans un pipeline data pour l'analyse contextuelle c'est quelque chose que je referai.

---

## Partie 4 — Ce que j'ai appris [5 min]

### Technique
- **La lazy evaluation de Spark** — pas juste théoriquement, mais vraiment ressentie : voir `.drop()` sur 75 colonnes prendre 0.3ms m'a fait comprendre comment Spark fonctionne en interne.
- **Débugger un système distribué** — quand quelque chose plante dans un pipeline Spark ou dans les callbacks Dash, l'erreur est rarement là où on la cherche. J'ai appris à lire des stack traces JVM et à utiliser le Spark UI.

### Méthode de travail
- **Lire la documentation avant de tester** — au début j'essayais des trucs au feeling. Les bugs les plus longs à résoudre (pattern-matching callbacks, dual epoch tracking) venaient de comportements que j'aurais trouvés en 10 minutes de lecture de doc.
- **Isoler les problèmes** — face à un bug, j'ai appris à réduire le scope : supprimer des fonctionnalités jusqu'à trouver ce qui plante, plutôt que de tout chercher en même temps.

### Autonomie
- Ce projet est le premier où j'ai dû assembler une stack complète sans exemple existant qui fait exactement la même chose. Il n'y a pas de tutorial "Kedro + Spark MLlib + nfstream + Dash streaming". J'ai dû lire plusieurs documentations en parallèle et faire des choix d'architecture moi-même.
- Ça m'a appris à être à l'aise avec l'incertitude — il n'y a pas toujours une bonne réponse, il faut choisir, tester, et assumer le choix.

### Gestion des difficultés
- Le bug du dual epoch tracking (trafic live qui disparaissait après la première attaque) m'a bloqué plusieurs heures. J'ai appris à ne pas paniquer face à un bug incompréhensible — poser le problème par écrit, tracer les valeurs une par une, et remonter à la source.
- Savoir quand arrêter d'ajouter des fonctionnalités. À un moment le projet fonctionnait bien et j'aurais pu continuer à rajouter des trucs indéfiniment. J'ai appris à reconnaître quand c'est "assez bien" et qu'il vaut mieux stabiliser que d'ajouter.

---

*Tim Delhez — HELMo BLOC 2 Q2 · 2025-2026*
