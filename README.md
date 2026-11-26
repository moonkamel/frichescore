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
