# Référencement (SEO) — étude du 28/11/2026

Source : OpenSEO (données Google France, volumes mensuels moyens), projet « FricheScore (FR) ».

## Volumes de recherche

| Mot-clé | Volume / mois | Difficulté (0-100) | Intention |
|---|---:|---:|---|
| géorisques | 22 200 | 24 | information |
| zan | 12 100 | 0 | information |
| foncier | 9 900 | 11 | information |
| basias | 4 400 | 0 | information |
| loi zan | 3 600 | 24 | information |
| basol | 2 900 | 2 | information |
| friche industrielle | 1 600 | 9 | information |
| casias | 1 600 | 20 | information |
| zéro artificialisation nette | 1 300 | 32 | information |
| urbansimul | 1 300 | 2 | information |
| établissement public foncier | 1 000 | 57 | information |
| bodacc liquidation | 720 | 19 | navigation |
| cartofriches | 590 | 1 | information |
| fonds friches | 320 | 0 | information |
| friches industrielles | 260 | 16 | information |
| friche urbaine | 210 | 7 | information |
| sobriété foncière | 170 | 0 | information |
| charge foncière | 140 | 0 | information |
| friche commerciale | 110 | 0 | information |
| reconversion friche industrielle | 50 | 0 | information |
| prospection foncière | 40 | 0 | commerciale (CPC 7,8 €) |
| logiciel prospection foncière | 10 | 42 | commerciale (CPC 12,6 €) |
| logiciel foncier | 10 | 12 | commerciale |

## Ce qu'on en tire

1. **Les requêtes « logiciel » sont rares mais chères** (CPC 8 à 13 €) : peu de recherches, mais très qualifiées. Concurrents
   en première page : Promolead, Kel Foncier, Prospect+, Lumia, Géoperso, Explore — tous orientés promoteurs, aucun sur les
   friches. La page d'accueil vise donc « logiciel / priorisation des friches industrielles » où la place est libre.
2. **Le trafic est informationnel** : ZAN, loi ZAN, BASIAS, BASOL, CASIAS, Géorisques, friche industrielle, Cartofriches.
   La première page pour « friche industrielle » ne contient que Wikipédia, des publications universitaires, l'ADEME et
   des articles : aucun outil. Difficulté faible (souvent < 25) : des **pages guides** bien faites peuvent s'y classer.
3. La page d'accueil reprend ces termes (titre, H1/H2, FAQ balisée en données structurées `FAQPage`), sans les répéter
   artificiellement.

## Pages guides à écrire ensuite (par priorité)

| Page | Mots-clés visés | Volume cumulé |
|---|---|---:|
| BASIAS, CASIAS, BASOL, SIS : lire les bases de pollution d'une friche | basias, basol, casias | ~8 900 |
| Loi ZAN : ce que la trajectoire change pour vos friches | zan, loi zan, zéro artificialisation nette | ~17 000 |
| Friche industrielle : définition, inventaire, reconversion | friche industrielle, friches industrielles, reconversion | ~1 900 |
| Cartofriches : utiliser l'inventaire national (et aller plus loin) | cartofriches, urbansimul | ~1 900 |
| Fonds friches / Fonds vert : calendrier et éligibilité | fonds friches, fonds vert friches | ~330 |

Chaque guide renvoie vers la démonstration ; l'outil sert de preuve (captures, exemples chiffrés).

## Technique (déjà en place)

- Titre, description, URL canonique, Open Graph, données structurées `SoftwareApplication` et `FAQPage`.
- `robots.txt` (l'application et l'API exclues) et `sitemap.xml`, à l'adresse définie par `FRICHESCORE_SITE_URL`.
- Page légère : pas de bibliothèque externe, images WebP avec dimensions, chargement différé hors écran.
- À faire à la mise en ligne : déclarer le site dans Google Search Console et y soumettre le sitemap ; renseigner le
  domaine dans le projet OpenSEO pour suivre les positions.
