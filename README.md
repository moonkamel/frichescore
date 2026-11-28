# FricheScore

**Moteur de décision pour la reconversion des friches.** FricheScore répond à une seule question :
*parmi les friches d'un territoire, lesquelles méritent qu'on ouvre une étude en premier, et pourquoi ?*

Ce n'est pas un portail cartographique de plus. La carte sert à naviguer ; la valeur est dans le **classement**,
son **explication** et la **traçabilité** des données qui le fondent.

## Démarrer (local, sans service cloud)

```bash
docker compose up --build
```

Puis ouvrir <http://localhost:8010> (port par défaut, modifiable avec la variable `FRICHESCORE_PORT`). Au premier lancement, un jeu de démonstration est chargé.
Les données (portfolio, saisies terrain, imports) sont conservées dans le volume Docker `frichescore-data`.

Sans Docker :

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8010
```

Tests : `pytest` (61 tests : règles de scoring, API de bout en bout, connecteurs avec réponses simulées,
import CSV tolérant, paramètres, calendrier).

> ⚠️ **Jeu de démonstration synthétique.** Les 32 communes et EPCI sont réels, mais les ~250 friches et leurs
> valeurs sont générées (graine fixe). Toutes les provenances portent la mention « (démo) » et un bandeau
> l'indique dans l'interface et les PDF. Importez vos données réelles via l'onglet **Données**.

## Ce que l'outil calcule

Chaque friche reçoit une note 0-100 sur cinq dimensions, **pour chacun des six usages**
(logement, industrie/logistique, tertiaire, photovoltaïque au sol, renaturation, équipement public) —
chaque usage a sa propre logique de scoring.

| Dimension | Ce qu'elle mesure | Sens |
|---|---|---|
| D1 Potentiel d'usage | Caractéristiques du site × besoins du territoire, logique propre à l'usage | ↑ favorable |
| D2 Coût de remise en état | Risque de surcoût : démolition/amiante, dépollution (diagnostic > BASOL/SIS > ICPE > BASIAS) modulée par la sensibilité de l'usage, VRD, géométrie, maîtrise foncière. Fourchette en € | ↑ favorable |
| D3 Friction réglementaire | Zones humides, captages, PPRi/PPRT, Natura 2000, ABF, évaluation environnementale, compatibilité PLU(i), servitudes, ICPE à cesser, multipropriété. Délai estimé en mois | **↑ pénalisant** |
| D4 Attractivité économique | Marché (DVF, dynamique, liquidité, SITADEL, vacance) + compte à rebours aménageur simplifié (bilan net en fourchette) | ↑ favorable |
| D5 Impact & financements *(optionnelle)* | Contribution ZAN, ACV/PVD, éligibilité présumée Fonds Vert, ADEME, France 2030, CRE, régions | ↑ favorable |

```
FricheScore = Σ poids × note / Σ poids − pénalité de confiance       (D3 entre sous la forme 100 − friction)
pénalité    = (1 − confiance) × 20
confiance   = niveau de la source (élevé 1 · moyen 0,75 · faible 0,45) × fraîcheur (1 → 0,6 au-delà de 10 ans)
```

Règles imposées :

- **Jamais de score sans explication** : les cinq notes et les trois facteurs qui ont le plus pesé sont toujours affichés.
- **Refus de scorer** sans localisation, surface et au moins une information sur la pollution ou l'usage passé (« non évaluable »).
- **Usages rédhibitoires** (zone rouge PPRi pour du logement, captage en protection immédiate…) : score plafonné à 10.
- **Incertitude assumée** : fourchettes plutôt que montants ponctuels, et recommandation d'études complémentaires.

Toutes les hypothèses chiffrées (coûts unitaires, seuils, profils) sont dans [`app/scoring/config.py`](app/scoring/config.py)
et doivent être calibrées sur vos propres opérations.

## Fonctionnalités

| Onglet | Usage |
|---|---|
| **Classement** | Périmètre (département, EPCI, liste de communes), usage cible, profils *Équilibré / Rentabilité / Rapidité / Impact*, curseurs de pondération recalculés en direct, filtres (ex. « photovoltaïque > 60 », confiance, coût max). Fiche détaillée par site : dimensions, coûts poste par poste, délais, bilan, financements, études recommandées, **fiche de vie de la donnée**, **contestation** d'une hypothèse (effet immédiat, rien d'enregistré), export PDF. |
| **Contre-analyse** | Scénarios défavorables (dépollution ×2, délais +12 mois, marché −15 %, sans subventions, lecture pessimiste des données incertaines, changement d'usage) : qui reste dans le top N, qui décroche et pourquoi. **Monte-Carlo** : probabilité de rester dans le top N en tirant les incertitudes propres à chaque site. |
| **Coûts cachés** | Part de sites « piégeux » et « pépites propres » par département, EPCI ou commune ; carte de repérage colorée par risque de surcoût. |
| **Horloge ZAN** | Échéances ZAN (SCoT 2027, PLU 2028, 2031) et fenêtres de financement, croisées avec la maturité estimée de chaque friche. |
| **Jumeau** | Deux friches (ou une friche et un **site en extension**) côte à côte, usage simulé différent sur chacune, verdict en trois lignes. |
| **Portfolio** | Pipeline repérée → pré-étudiée → en étude → abandonnée (glisser-déposer). Les **données terrain** saisies priment sur les bases nationales et font évoluer le score et le rang ; historique conservé. |
| **Données** | Import CSV tolérant avec **vérification sans import**, **test de connexion** réel aux API, import d'un périmètre par codes INSEE. |
| **Paramètres** | Coûts unitaires, VRD par usage, dépollution par signal (BASOL, BASIAS, ICPE…), sensibilité des usages, pénalités et délais de friction, confiance, seuils, profils : modifiables sans toucher au code, avec une justification par valeur, export/import JSON. |
| **Exports** | Fiche de synthèse PDF par site, **rapport de pré-cadrage** PDF (short-list, contre-analyse, justification site par site, non-évaluables, annexe des sources), CSV du classement. |

## Données

| Source | Rôle | Accès |
|---|---|---|
| Cartofriches (API Données foncières Cerema) | Inventaire de base | Connecteur API + import CSV |
| Géorisques : CASIAS (ex-BASIAS), instructions SSP (ex-BASOL), SIS, ICPE | Coût et friction | Connecteur API |
| DVF géolocalisé | Attractivité | Connecteur (fichiers geo-dvf par commune) |
| API Géo (geo.api.gouv.fr) | Population, EPCI, mairie → distance au centre de chaque friche | Connecteur API |
| Zonage ABC, LOVAC (vacance), SITADEL (logements autorisés) — data.gouv.fr | Potentiel logement, marché | Fichiers nationaux résolus via l'API data.gouv.fr, mis en cache 30 jours |
| Consommation d'ENAF 2011-2021 (Cerema, `/indicateurs/conso_espace`) | Impact ZAN | Connecteur API |
| Gares de voyageurs — SNCF open data | Distance à la gare de chaque friche | Export CSV, cache 90 jours (repli OpenStreetMap) |
| Arrêts de transport — OpenStreetMap via Overpass (© contributeurs OSM, ODbL) | Desserte en transports collectifs | Une requête par commune |
| PVGIS 5.3 (Commission européenne) | Ensoleillement (photovoltaïque) | API, carreaux de 0,1° |
| Rapport de risques / GASPAR — Géorisques | Risque d'inondation, aléa argiles | API (1 appel/s), repli commune |
| Géoportail de l'urbanisme — API Carto IGN | Zone et vocation PLU, servitudes | API, au point de chaque friche |
| INPN — API Carto IGN (module Nature) | Natura 2000, ZNIEFF | API, au point |
| Cartographie nationale des milieux humides (OFB/PatriNat) — Géoplateforme | Zone humide probable (prélocalisation) | WFS, couche trouvée dans le catalogue |
| Base permanente des équipements — INSEE | Déficit d'équipements (enseignement, santé, sport-culture / moyenne nationale) | Fichier national, cache 30 jours |
| Cadastre / fichiers fonciers, PLU(i) (GPU)… | Autres dimensions | Import CSV (une colonne par indicateur) |

### Connecteurs

Onglet **Données → Tester la connexion** : appel réel sur une commune de test, avec l'URL appelée, le code HTTP,
la durée, le résultat interprété et un extrait de la réponse brute. L'adresse de chaque API est modifiable dans le
formulaire (ou par les variables `CARTOFRICHES_URL`, `GEORISQUES_URL`, `GEODVF_URL`) si elle a changé.

Les erreurs (délai dépassé, proxy, HTTP 4xx/5xx, JSON illisible, format inattendu) sont traduites en messages clairs
et ne font jamais planter l'outil. Un échec partiel n'invente rien : si l'API SIS ne répond pas, l'indicateur SIS
reste « non renseigné » (et pèse sur la confiance), il n'est jamais mis à « non » par défaut.

Le parsing est couvert par des tests avec des réponses simulées calquées sur les formats publics
(`tests/test_connectors.py`, `tests/fixtures/`). **Les connecteurs n'ont pas encore été validés contre les API
réelles** (accès réseau bloqué pendant le développement) : c'est la première chose à faire avec le bouton de test.

### Indicateurs communaux

Calculés automatiquement à l'import d'un périmètre, ou via **Données → Mettre à jour les indicateurs communaux**
(pour les communes portant des friches réelles, par exemple après un import CSV). Les fichiers nationaux ne sont
pas désignés par une adresse figée : l'API de data.gouv.fr fournit la ressource la plus récente de chaque jeu de
données, téléchargée une fois et mise en cache (`<dossier de la base>/cache/`, 30 jours, `FRICHESCORE_CACHE_JOURS`).
Les colonnes sont détectées de façon tolérante ; la source « Indicateurs communaux » du test de connexion montre le
fichier retenu, les colonnes détectées et la valeur obtenue pour une commune. Une friche réelle n'hérite jamais
d'indicateurs de démonstration.

### Indicateurs de site

Calculés à partir des coordonnées de chaque friche, à l'import d'un périmètre ou via **Données → Mettre à jour
les indicateurs de site**. Desserte : *forte* si un métro, tram ou train est à moins de 600 m ou si au moins 8 arrêts
sont à moins de 400 m ; *moyenne* à partir de 2 arrêts ; *faible* sinon. Le fichier national d'arrêts de
transport.data.gouv.fr (~417 Mo, non dédoublonné) n'est pas utilisé : OpenStreetMap est interrogé commune par commune.
Un risque d'inondation recensé sans zonage PPRi connu ajoute de la friction (zonage à vérifier) ; un aléa argiles fort
ajoute un surcoût de fondations — deux paramètres réglables.

### Import CSV

- Séparateur (`;` `,` tabulation `|`) et encodage (UTF-8, Windows/Excel) détectés automatiquement.
- En-têtes acceptés : clé (`surface_m2`), libellé (« Surface du site (m²) ») ou nom Cartofriches (`site_surface`).
- Formats tolérés : `12 000,5`, `1 500 m²`, `oui/non/vrai/faux/1/0`, zones PLU `1AUh` → `AU`, accents et majuscules.
- **Vérifier sans importer** produit un rapport « ligne X, colonne Y, problème Z », exportable en CSV. Une cellule
  invalide est ignorée sans bloquer la ligne ; une ligne n'est rejetée que si elle n'a ni code INSEE ni coordonnées.
- Modèle : `GET /api/import/modele.csv` ; dictionnaire des colonnes : `GET /api/import/dictionnaire.csv`.
- Provenance par colonne : ajouter `<colonne>__source`, `<colonne>__date`, `<colonne>__confiance`.

### Quelle étude lancer en premier ?

Onglet **Contre-analyse**. Pour chaque site proche de la limite du top N et chaque étude préalable possible (diagnostic
de sol, visite du bâti, délimitation des zones humides, étude foncière, analyse du PLU, zonage PPRi), l'outil simule les
résultats plausibles de l'étude avec des probabilités a priori affichées (un site BASOL a plus de chances de révéler une
pollution forte qu'un site sans signal), recalcule le score et mesure la **probabilité que l'étude change la sélection**.
Avec un budget d'études, il propose le programme qui maximise les bascules attendues par euro dépensé. Les coûts des
études se règlent dans Paramètres ; les probabilités a priori sont des hypothèses de travail, pas des statistiques.

### Signaux d'opportunité

Onglet **Signaux** (le nombre de signaux non lus s'affiche sur l'onglet, et un repère ◆ dans le classement).
À chaque mise à jour, FricheScore compare les données avant / après et signale : cessation d'activité d'une
installation classée, zonage du PLU modifié (vers plus ou moins constructible), projet apparu sur le site, zone humide
ou zone rouge de PPRi nouvellement recensée, friche sortie de Cartofriches. Il interroge aussi le **BODACC** (annonces
légales, DILA, données ouvertes) pour l'entreprise propriétaire ou exploitante du site : procédure collective
(sauvegarde, redressement, liquidation), vente ou cession, radiation. L'entreprise est identifiée par son SIREN :
exploitants ICPE (Géorisques) et société propriétaire (colonne `siren_proprietaire` du CSV ou saisie terrain). Seuls
des SIREN sont utilisés : aucun nom n'est repris des annonces, la dénomination d'une entreprise individuelle étant le
nom d'une personne. Le BODACC peut être testé dans Données → Tester la connexion.

### Calendrier des financements

Éditable dans **Horloge ZAN → Modifier le calendrier** (formulaire ou JSON avancé, validé avant enregistrement).
Chaque dispositif porte un statut **vérifié / à vérifier**, une date de vérification et l'adresse de la page officielle ;
la frise et les correspondances peuvent être filtrées par type de financement et limitées aux fenêtres vérifiées.
Le fichier est stocké à côté de la base (`funding_windows.json`, dans le volume Docker). Les échéances ZAN viennent
des textes ; **les fenêtres livrées par défaut sont des exemples à remplacer par les calendriers officiels.**

### Paramètres

Les valeurs par défaut sont dans [`app/scoring/config.py`](app/scoring/config.py) ; les valeurs calibrées sont
stockées en base et s'appliquent immédiatement. Le rapport de pré-cadrage liste en annexe les paramètres modifiés
et leur justification. Les règles de pertinence par usage (dimension 1 : seuils de surface, distances…) restent
dans [`app/scoring/usages.py`](app/scoring/usages.py).

### Mise à jour automatique et packs de données

**Données → Mise à jour automatique et packs de données.** Chaque nuit (heure réglable, 3 h par défaut, fuseau
Europe/Paris), le serveur remet la base à jour pour que personne n'attende les API publiques :

- **mode API publiques** : réinterroge Cartofriches, Géorisques, DVF et les indicateurs communaux et de site pour les
  communes portant déjà des friches Cartofriches et pour **toutes les communes des départements listés** (comptez
  plusieurs heures pour un département entier la première nuit) ; rafraîchit aussi les indicateurs des friches importées
  par CSV ;
- **mode packs** : télécharge le pack de chaque département listé auprès d'un serveur FricheScore central et l'importe
  en quelques secondes.

Le journal indique pour chaque passage les **nouvelles friches**, les **scores qui ont bougé d'au moins 2 points**
(avant / après) et les friches **absentes de Cartofriches** (conservées, à vérifier). Une mise à jour peut aussi être
lancée à la main ; si le serveur était éteint à l'heure prévue, elle part au démarrage suivant de la journée.

**Packs** : un pack (`.json.gz`) contient les friches réelles d'un ou plusieurs départements et les indicateurs des
communes concernées — jamais les saisies terrain, le portfolio, les documents ni les notes IA. Export et import depuis
l'interface (vérification possible avant import ; une friche de même identifiant venant d'une autre source n'est pas
écrasée). En cochant « publier les packs », le serveur devient **serveur central** : il écrit un pack par département
après chaque mise à jour et le sert à l'adresse `/api/packs/<département>.json.gz`. À exposer derrière un accès
authentifié (proxy inverse) si le serveur est joignable depuis Internet : FricheScore n'a pas de gestion de comptes.

En ligne de commande (tâche planifiée du système, par exemple) :

```bash
docker compose exec frichescore python -m app.maj --departements 59 62 --publier
```

`FRICHESCORE_PLANIFICATEUR=0` désactive le planificateur intégré.

## Mise en ligne : organismes et comptes

Par défaut, FricheScore fonctionne **en local** pour un seul poste, sans connexion. Sur le serveur en ligne, activer
`FRICHESCORE_AUTH=1` : chaque utilisateur se connecte (adresse et mot de passe) et appartient à un **organisme client**.

- **Base commune**, partagée et mise à jour la nuit : friches Cartofriches, indicateurs communaux et de site, signaux
  publics (cessations ICPE, PLU, BODACC des exploitants ICPE).
- **Espace privé de chaque organisme**, jamais visible des autres : friches importées par CSV, saisies terrain,
  portfolio, historique des scores, documents et extractions IA, notes IA, paramètres calibrés, calendrier des
  financements, signaux issus de ses propres SIREN, statut « lu » des signaux, tâches de fond, quota IA.
- **Rôles** : lecteur (consultation, exports), analyste (saisies, imports, portfolio, documents, IA), administrateur de
  l'organisme (en plus : comptes, paramètres, calendrier), administrateur de la plateforme (organismes, mise à jour
  nocturne, packs). L'administrateur de la plateforme voit la base commune, pas les données privées des clients.
- Sécurité : mots de passe hachés (scrypt), 12 caractères minimum ; session de 12 h dans un cookie `HttpOnly`,
  `SameSite=Lax`, réservé au HTTPS (`FRICHESCORE_COOKIE_SECURE=1`) ; blocage de 15 min après 5 échecs de connexion ;
  changement de mot de passe ou désactivation = sessions fermées. Les données de démonstration ne sont pas chargées.

Premiers comptes (sur le serveur) :

```bash
docker compose exec frichescore python -m app.auth creer-admin --email vous@exemple.fr
```

```bash
docker compose exec frichescore python -m app.auth creer-organisme --id epf-hdf --nom "EPF Hauts-de-France" --admin-email chef@epf.fr
```

Ensuite, tout se gère depuis l'onglet **Organismes** (plateforme) ou **Comptes** (administrateur d'un organisme).
Une base locale existante est migrée automatiquement : ses données deviennent celles de l'organisme « local ».
Reste à faire avant une mise en production : base PostgreSQL (SQLite convient pour quelques organismes),
sauvegardes, HTTPS par un proxy inverse, contrat de sous-traitance RGPD avec chaque client.

## Page de présentation publique

`site/index.html` : page d'accueil commerciale (fonctionnalités, méthode, public visé, sources, FAQ, formulaire de
démonstration). En ligne (`FRICHESCORE_AUTH=1`), elle est servie à la racine `/` et l'application passe sous `/app` ;
en local, elle reste consultable sous `/presentation`. Les demandes de démonstration sont enregistrées en base et
listées dans l'onglet **Organismes** (administrateur de la plateforme). Régler `FRICHESCORE_SITE_URL`
(ex. `https://www.mondomaine.fr`) pour l'URL canonique, `robots.txt` et `sitemap.xml`. Les mentions légales
(`site/mentions-legales.html`) contiennent des champs **[À compléter]** avant publication. Étude de mots-clés :
[`docs/referencement.md`](docs/referencement.md).

## Extraction de documents par l'IA (optionnelle)

Dans la fiche d'une friche, **Documents — analyse par IA** : déposez un PDF (étude de sol, diagnostic, acte, courrier
de la DREAL…). L'API Claude (modèle `claude-opus-5-5` par défaut) **propose** des valeurs d'indicateurs, des mesures
chiffrées et des points de vigilance, chacun avec **sa page et sa citation exacte**. Rien n'entre dans le score sans
validation : seules les valeurs cochées deviennent des données terrain, tracées « « fichier », p. 14 — extrait par IA,
validé par … ». Une valeur sans citation n'est pas proposable. Le PDF est conservé localement pour vérification.

Activation : créer un fichier `.env` à côté de `docker-compose.yml` (jamais versionné) contenant
`ANTHROPIC_API_KEY=...`, puis `docker compose up -d`. Sans clé, FricheScore fonctionne comme avant.
Réglages : `FRICHESCORE_IA_QUOTA_MOIS` (200 documents par défaut), `FRICHESCORE_IA_MODELE`, `FRICHESCORE_IA_EFFORT`.
Le coût estimé de chaque analyse est affiché et cumulé par mois.

Avec la même clé :

- **Contre-analyse en langage naturel** (onglet Contre-analyse) : « et si la mairie exigeait une zone humide
  compensatoire, avec 500 k€ du Fonds Vert ? ». L'IA traduit la question en hypothèses du moteur (affichées et
  reprenables dans les curseurs), signale ce qui n'est pas modélisable ; le calcul reste celui du moteur.
- **Note d'analyse** (fiche d'une friche) et **conclusion argumentée** (Jumeau) : rédigées uniquement à partir du
  dossier FricheScore du site. Chaque chiffre cité est recherché dans ce dossier ; ceux qu'on n'y retrouve pas sont
  signalés « à vérifier ». La note figure dans le PDF de la fiche avec la mention « rédigée par IA, à relire ».

⚠️ Les documents sont transmis à l'API Anthropic : une case de consentement est obligatoire à chaque envoi. Pour les
documents contenant des données personnelles (actes de propriété), vérifier l'accord du client et les conditions de
traitement des données, ou masquer ces données avant l'envoi. L'IA a pour consigne de ne jamais restituer le nom d'une
personne physique.

## Architecture

```
app/
  scoring/      moteur : record.py (donnée tracée), usages.py (D1), cost.py (D2), friction.py (D3),
                market.py (D4), impact.py (D5), engine.py (assemblage, confiance, explication),
                stress.py (scénarios, Monte-Carlo), compare.py (jumeau), analytics.py (coûts cachés, ZAN)
  ingest/       connecteurs API et import CSV
  reports/      PDF (reportlab)
  demo/         générateur du jeu de démonstration
  main.py       API FastAPI · service.py orchestration · db.py SQLite
frontend/       interface HTML/CSS/JS sans dépendance externe (fonctionne hors ligne)
```

Le fond de carte (contours départementaux simplifiés) provient d'IGN Admin Express COG 2018 via
[gregoiredavid/france-geojson](https://github.com/gregoiredavid/france-geojson), sous Licence Ouverte Etalab ;
il est embarqué dans `frontend/departements.json` pour fonctionner hors ligne.

Le score global est recalculé dans le navigateur quand on bouge les curseurs (`frontend/app.js : globalScore`,
miroir de `engine.global_score`) ; tout le reste est calculé côté serveur.

## Limites assumées

FricheScore hiérarchise des sites pour décider où ouvrir une étude. Il ne remplace ni un diagnostic de pollution,
ni une étude de sol, ni un pré-bilan d'aménagement, ni la vérification d'éligibilité auprès d'un financeur.
