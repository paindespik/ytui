# Tests E2E du lecteur web (manuels)

Ces deux scripts pilotent un **vrai Chromium** sur un **vrai backend** et de
**vraies vidéos YouTube**. Ils ne tournent pas en CI (réseau + navigateur
requis) : ce sont les tests à relancer quand on touche à `js/player.js`,
`js/router.js` ou `views/watch.js`.

Ils couvrent ce que les tests unitaires ne peuvent pas voir : dash.js qui
ignore une consigne de reprise, une fin de média émise deux fois, un lecteur
qui survit à une navigation.

## Backend jetable

**Ne jamais viser le serveur de production** : ces scripts écrivent dans
l'historique. Toujours lancer une instance locale avec une base temporaire.

```sh
cd backend
YTUI_API_TOKEN=e2e-token YTUI_DATA_DIR=/tmp/ytui-e2e \
  python -m uvicorn ytui_server.main:app --host 127.0.0.1 --port 8791
```

## Lancement

```sh
# Reprise d'une vidéo partiellement vue (3 manches)
node backend/ytui_server/web/tests/e2e/resume.mjs

# Enchaînement d'une file de lecture de 3 vidéos
node backend/ytui_server/web/tests/e2e/playlist.mjs
```

Variables utiles :

| Variable | Rôle | Défaut |
|---|---|---|
| `YTUI_BASE` / `YTUI_TOKEN` | backend visé | `http://127.0.0.1:8791` / `e2e-token` |
| `PLAYWRIGHT_MODULE` | chemin du module Playwright | résolution automatique |
| `CHROMIUM` | binaire du navigateur (H.264 requis) | `/usr/bin/chromium` |
| `ROUNDS` / `IDS` | nombre de manches / vidéos de la file | 3 / 3 vidéos stables |
| `SLOW=1`, `KBPS`, `LATENCY` | bride le réseau via CDP | — |

## Le mode `SLOW` n'est pas décoratif

Sur un réseau local rapide, une reprise cassée passe quand même : le seek posé
sur l'élément arrive avant que le moteur n'ait fini de se mettre en place. Les
régressions de reprise ne se voient que sous latence :

```sh
SLOW=1 KBPS=3000 LATENCY=900 PLAY_TIMEOUT=90000 node .../e2e/resume.mjs
```

C'est dans ce mode qu'ont été trouvés le lecteur zombie (pulsation qui
réécrivait une position périmée) et la reprise perdue de dash.js.
