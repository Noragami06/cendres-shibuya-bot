# TODO — Cendres de Shibuya Bot

## Blocages actuels (le joueur reste coincé)

- [ ] Hybride → Chez les fléaux : après le tirage de la réserve (sans nature), aucun bouton "Continuer" n'existe. Chemin actuellement sans issue.
- [ ] Après le choix de récompense (Exorciste / Hybride-exorciste) : rien n'enchaîne vers l'étape suivante (RCT).

## Étapes du parcours /depart pas encore développées

- [ ] Le RCT (point 5 du programme) : aucun code pour aucun camp.
- [ ] La fiche finale (point 6/7) : n'existe pas, tous les chemins qui doivent y mener s'arrêtent avant.
- [ ] Comportement du joueur sans le rôle requis pour /depart ("point A", jamais précisé).
- [ ] L'étape Territoire elle-même (pas juste le reroll en récompense).

## Systèmes incomplets

- [ ] Argent / XP obtenus en récompense : enregistrés mais aucun système économique/niveau ne leur donne d'effet réel.
- [ ] Objets (reliques, armes) obtenus en récompense : enregistrés mais aucun système d'inventaire n'existe.
- [ ] Système de niveau général (Level/XP) : actuellement fixé à niveau 1 / 0 XP pour tout le monde à la création, formule temporaire (N*1000 XP par niveau) en attendant le vrai système.
- [ ] Force / Vitesse / Défense : actuellement fixées à niveau 1 / 0% pour tout le monde à la création, doivent être liées à un futur système de statistiques pas encore développé. Les 4 Maîtrises (EO/Sort/Territoire/RCT) dépendront aussi de ce futur système une fois prêt.
- [ ] Définir combien de points de stats un personnage reçoit à la création de sa fiche (actuellement `points_restants` = 0 par défaut dans character_stats).
- [x] Système de stockage des relations (famille/amis/autres) : table `character_relations` + ajout/retrait par le joueur (sur son perso) ou le staff (sur n'importe qui), via les boutons de la page 🤝 Relation.

## Divers

- [ ] Livré à soi même : système de récompense propre à ce chemin pas encore défini.
- [ ] Reroll Territoire / Reroll RCT (récompenses) : aucun effet réel tant que ces étapes n'existent pas.
