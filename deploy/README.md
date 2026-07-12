# Déploiement du backend ytui sur `server`

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

# 4. nginx + certificat (le DNS *.example.com pointe déjà sur le serveur)
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
| `DEPLOY_USER` | `deploy` |

## Opérations courantes

```sh
docker compose -f deploy/docker-compose.yml logs -f ytui-backend   # logs
docker compose -f deploy/docker-compose.yml up -d --build          # redeploy manuel
docker compose -f deploy/docker-compose.yml down                   # stop
```
