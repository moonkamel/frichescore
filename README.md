# FricheScore

**Moteur de décision pour la reconversion des friches.** FricheScore répond à une seule question :
*parmi les friches d'un territoire, lesquelles méritent qu'on ouvre une étude en premier, et pourquoi ?*

Ce n'est pas un portail cartographique de plus. La carte sert à naviguer ; la valeur est dans le **classement**,
son **explication** et la **traçabilité** des données qui le fondent.

## Démarrer (local, sans service cloud)

```bash
docker compose up --build
```

Puis ouvrir <http://localhost:8000>. Au premier lancement, un jeu de démonstration est chargé.
Les données (portfolio, saisies terrain, imports) sont conservées dans le volume Docker `frichescore-data`.

Sans Docker :

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Tests : `pytest` (34 tests : règles de scoring, API de bout en bout, parseurs des connecteurs).

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
| **Exports** | Fiche de synthèse PDF par site, **rapport de pré-cadrage** PDF (short-list, contre-analyse, justification site par site, non-évaluables, annexe des sources), CSV du classement. |

## Données

| Source | Rôle | Accès |
|---|---|---|
| Cartofriches (API Données foncières Cerema) | Inventaire de base | Connecteur API + import CSV |
| Géorisques : CASIAS (ex-BASIAS), instructions SSP (ex-BASOL), SIS, ICPE | Coût et friction | Connecteur API |
| DVF géolocalisé | Attractivité | Calcul d'indicateurs à partir des fichiers geo-dvf (`app/ingest/connectors.py`) |
| Cadastre / fichiers fonciers, SITADEL, INSEE/LOVAC, PLU(i) (GPU), portail de l'artificialisation, PVGIS… | Autres dimensions | Import CSV (une colonne par indicateur) |

- Modèle d'import : `GET /api/import/modele.csv`. Les exports Cartofriches sont reconnus (`site_id`, `site_nom`, `comm_insee`, `site_surface`, `site_type`, `bati_surface`…).
- Provenance par colonne : ajouter `<colonne>__source`, `<colonne>__date`, `<colonne>__confiance`.
- Le calendrier des financements est un fichier éditable : [`app/data/funding_windows.json`](app/data/funding_windows.json).
  Les échéances ZAN viennent des textes ; **les fenêtres de financement sont des exemples à vérifier** auprès des financeurs.

Les connecteurs Cartofriches/Géorisques demandent un accès Internet. Ils ont été écrits d'après la documentation
publique et testés sur des réponses types, mais **pas contre les API réelles** (accès réseau indisponible pendant
le développement) : à valider sur un premier périmètre avant usage en production.

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

Le score global est recalculé dans le navigateur quand on bouge les curseurs (`frontend/app.js : globalScore`,
miroir de `engine.global_score`) ; tout le reste est calculé côté serveur.

## Limites assumées

FricheScore hiérarchise des sites pour décider où ouvrir une étude. Il ne remplace ni un diagnostic de pollution,
ni une étude de sol, ni un pré-bilan d'aménagement, ni la vérification d'éligibilité auprès d'un financeur.
