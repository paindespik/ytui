# Déploiement du backend ytui

Le backend tourne en Docker (compose, `restart: unless-stopped`), exposé en loopback
sur `127.0.0.1:8776` derrière le nginx natif de l'hôte sur `https://ytui.example.com`.

## Installation initiale (une fois)

```sh
# 1. Cloner le repo
git clone ssh://git@git.example.com/paindespik/ytui.git ~/ytui-deploy
cd ~/ytui-deploy

# 2. Token d'API
echo "YTUI_API_TOKEN=$(openssl rand -hex 32)" > deploy/.env
chmod 600 deploy/.env

# 3. Lancer le backend
docker compose -f deploy/docker-compose.yml up -d --build
curl http://127.0.0.1:8776/health   # → {"status":"ok",...}

# 4. nginx + certificat (le DNS de votre domaine doit pointer sur le serveur)
sudo cp deploy/nginx-ytui.conf /etc/nginx/sites-enabled/ytui.conf
sudo certbot certonly --webroot -w /var/www/letsencrypt -d ytui.example.com
sudo nginx -t && sudo systemctl reload nginx
```

## Données

- `deploy/data/` (volume `/data` du conteneur) : `meta.sqlite`, `oauth_token.json`,
  `client_secret.json` (OAuth Google pour like/comment).
- Migration depuis un poste desktop : `backend/scripts/import_local.py`, ou copier
  le `~/.cache/ytui/meta.sqlite` local dans `deploy/data/`.
- Like/comment : `ytui auth push` depuis le desktop (flow OAuth local, upload du token).

## Déploiement continu

La CI Forgejo (`.forgejo/workflows/ci.yml`) déploie à chaque push sur `master` :
le job `deploy` se connecte en ssh (clé dédiée `~/.ssh/ytui_deploy`, restreinte par
`command=` dans `authorized_keys`) et exécute `deploy/deploy.sh` qui fait
`git reset --hard origin/master` + `docker compose up -d --build` + attente du health.

Secrets Forgejo requis (repo → Settings → Actions → Secrets) :

| Secret | Valeur |
|---|---|
| `DEPLOY_SSH_KEY` | clé privée dédiée (la publique est dans `authorized_keys` de deploy@server) |
| `DEPLOY_HOST` | IP de l'hôte joignable depuis le conteneur runner (ex. `172.17.0.1`) |
| `DEPLOY_USER` | `<utilisateur ssh du serveur>` |

## Opérations courantes

```sh
docker compose -f deploy/docker-compose.yml logs -f ytui-backend   # logs
docker compose -f deploy/docker-compose.yml up -d --build          # redeploy manuel
docker compose -f deploy/docker-compose.yml down                   # stop
```

## Backups & maintenance

Deux timers systemd *user* sur le serveur :

- `ytui-backup.timer` — snapshot quotidien (04:00) de `meta.sqlite` via l'API backup
  de SQLite (compatible WAL, exécuté dans le conteneur), rétention 14 jours.
  Les fichiers atterrissent dans `deploy/data/backups/` (volume `/data`).
- `ytui-refresh.timer` — rebuild hebdomadaire (dimanche 05:00) de l'image Docker
  sans cache (`deploy/deploy.sh --no-cache`) pour embarquer le dernier yt-dlp.

```sh
mkdir -p ~/.config/systemd/user
cp deploy/ytui-backup.{service,timer} deploy/ytui-refresh.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ytui-backup.timer ytui-refresh.timer
loginctl enable-linger $USER   # obligatoire : timers user sans session ouverte
```

Backup manuel : `bash deploy/backup.sh` (crée `deploy/data/backups/meta-<date>.sqlite`).
Vérifier : `systemctl --user list-timers`.

## Session YouTube (suggestions personnalisées)

`GET /api/suggestions` sert la page d'accueil YouTube du compte quand des
cookies de session sont présents. Ces cookies ne peuvent pas être entretenus par
le backend : YouTube lie la session à un cookie `__Secure-1PSIDTS` que seul son
détenteur peut renouveler — youtube.com ne le renvoie jamais (il ne rafraîchit
que `SIDCC`/`__Secure-*PSIDCC`) et l'endpoint de rotation de Google refuse les
appels tiers. Pire, un second détenteur qui rotate le jeton périme toutes les
autres copies.

D'où un navigateur dédié **sur le serveur**, qui ne sert qu'à détenir la session
et à la renouveler, plus un timer qui recopie le résultat dans le backend :

- `ytui-browser.service` — Firefox headless permanent sur le profil
  `~/ytui-cookies/profile`, ouvert sur youtube.com.
- `ytui-cookies.timer` — toutes les 10 min, `deploy/refresh_cookies.py` lit les
  cookies youtube.com du profil et les POSTe sur `/api/auth/youtube/cookies`.
  Le backend valide et sonde le candidat avant de remplacer la session en place :
  un refus laisse intacte celle qui fonctionne.

Connexion initiale (une seule fois, le serveur n'a pas d'écran) — depuis un poste
avec serveur X, `X11Forwarding yes` étant activé côté sshd :

```sh
ssh serv 'firefox --headless --no-remote --CreateProfile "ytui $HOME/ytui-cookies/profile"'
ssh -X serv 'firefox --no-remote --profile ~/ytui-cookies/profile \
  "https://accounts.google.com/ServiceLogin?service=youtube"'
# se connecter dans la fenêtre, puis la fermer
cp deploy/ytui-browser.service deploy/ytui-cookies.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ytui-browser.service ytui-cookies.timer
```

**Ne jamais naviguer ailleurs avec ce compte dans ce profil** : le jeton serait
volé par l'autre navigateur. Un compte Google accepte plusieurs sessions
simultanées, donc le navigateur habituel n'est pas affecté.

Diagnostic : `journalctl --user -u ytui-cookies -n 20`. Si l'onglet Suggestions
revient aux vidéos liées à l'historique, le champ `warnings` de `/api/suggestions`
le dit explicitement.
