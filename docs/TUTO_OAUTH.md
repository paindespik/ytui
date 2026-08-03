# Tutoriel : Connexion YouTube (OAuth2) avec ytui

Ce tutoriel explique comment configurer la connexion OAuth2 à YouTube pour liker, lire et poster des commentaires depuis ytui (touches **L** / **C** dans la TUI, boutons 👍 et « Commentaires » dans l'app mobile et le navigateur).

---

## 1. Préparer le projet Google Cloud Console

### 1.1 Créer un projet GCP

1. Ouvrez [Google Cloud Console](https://console.cloud.google.com/).
2. Cliquez sur le sélecteur de projet en haut à gauche → **Nouveau projet**.
3. Donnez-lui un nom (ex. `ytui`) et cliquez sur **Créer**.

### 1.2 Activer l'API YouTube Data v3

1. Allez dans **APIs & Services** → **Bibliothèque**.
2. Recherchez **YouTube Data API v3**.
3. Cliquez dessus, puis sur **Activer**.

### 1.3 Configurer l'écran de consentement OAuth

1. Allez dans **APIs & Services** → **Écran de consentement OAuth**.
2. Type d'utilisateur : **Externe** → **Créer**.
3. Remplissez les champs obligatoires (nom de l'application, email support).
4. Cliquez sur **Enregistrer et continuer** jusqu'au bout.
5. Onglet **Utilisateurs de test** → **Ajouter des utilisateurs** → tapez votre adresse Gmail → **Enregistrer**.

> ⚠️ Le mode *Testing* signifie que seul les utilisateurs ajoutés comme « testeurs » peuvent authentifier l'application. Les jetons (tokens) expirent aussi après **7 jours** en mode testing. Pour une durée de vie illimitée, il faut soumettre l'application à la vérification Google et la publier.

### 1.4 Créer le secret OAuth (Desktop app)

1. Allez dans **APIs & Services** → **Identifiants**.
2. Cliquez sur **+ CRÉER DES IDENTIFIANTS** → **ID client OAuth**.
3. Type d'application : **Application de bureau (Desktop app)**.
4. Cliquez sur **Créer**, puis sur **Télécharger le JSON** (nommé `client_secret_*.json`).

### 1.5 Placer le fichier `client_secret.json`

Par défaut, ytui cherche le fichier ici :

```
~/.config/ytui/client_secret.json
```

Vous pouvez aussi spécifier un autre emplacement dans `~/.config/ytui/config.toml` :

```toml
[auth]
client_secret = "/chemin/vers/votre/client_secret.json"
```

---

## 2. Installer les dépendances optionnelles

```sh
pip install 'ytui[auth]'
# ou, si vous avez installé ytui via pipx :
pipx inject ytui google-auth google-auth-oauthlib google-api-python-client
```

Les paquets installés sont :
- `google-auth`
- `google-auth-oauthlib`
- `google-api-python-client`

---

## 3. Première connexion (flux OAuth2)

Lancez ytui et sélectionnez une vidéo YouTube, puis appuyez sur **L** (like) ou **C** (commenter).

1. La première fois, un navigateur s'ouvre automatiquement (serveur local sur un port aléatoire) pour vous demander d'autoriser l'accès.
2. Acceptez la demande → le navigateur affiche une page de confirmation.
3. ytui enregistre le jeton dans :

```
~/.config/ytui/oauth_token.json
```

   avec les permissions `chmod 600` (lecture/écriture uniquement pour vous).

4. Les connexions suivantes utilisent ce token automatiquement (rafraîchissement silencieux si besoin).

---

## 4. Comment liker et commenter dans ytui (TUI)

### 4.1 Liker (touche **L**)

- **Où** : dans n'importe quelle liste de vidéos (flux, recherche, chaîne, playlist) **ou** dans l'écran détails d'une vidéo.
- **Action** : la touche **L** envoie immédiatement un like sur YouTube.
- **Retour UX** :
  - Une notification apparaît : `Liking: <titre de la vidéo>`.
  - Un thread séparé (`group="youtube-auth"`) exécute l'appel API en arrière-plan.
  - Si le like réussit : notification `Liked: <titre>`.
  - En cas d'erreur (AuthError / ApiError) : notification d'erreur avec le message explicatif.
- **Limitations** : fonctionne uniquement sur les vidéos YouTube (`platform == "youtube"` et `kind == "video"`). Les chaînes, playlists et BitChute affichent un avertissement.

### 4.2 Commenter (touche **C**)

- **Où** : même contexte que le like (liste ou écran détails).
- **Action** : la touche **C** ouvre un modal de saisie de texte :

  ```
  Comment on: <titre de la vidéo>
  [__________________________]
  [  OK  ]  [Annuler]
  ```

- Après validation, le commentaire est envoyé via `commentThreads.insert` dans un thread `youtube-auth`.
- **Retour UX** :
  - Succès : `Comment posted on: <titre>`.
  - Erreur : message d'erreur (AuthError / ApiError).
- **Limitations** : mêmes restrictions que le like (YouTube uniquement). De plus, certaines vidéos ont les commentaires désactivés par leur créateur → l'API renvoie une erreur 403.

---

## 4bis. Dans l'app mobile et le navigateur

Ces deux clients passent par le même jeton côté serveur, et exposent les actions **pendant la lecture** (hors plein écran) :

- **👍 J'aime / 👍 Aimé** : bascule. L'état initial est lu via `GET /api/videos/{id}/rating` ; un second appui envoie `rating=none` et retire le like.
- **Commentaires** : remplace la file d'attente et les suggestions par la liste des commentaires **sans interrompre la lecture** (le mobile garde la même surface vidéo, le web ne touche pas à l'élément `<video>`). Pagination par curseur opaque (bouton « Plus » / chargement automatique en bas de liste sur mobile).
- **Publier** : le champ de saisie en bas du panneau envoie `POST /api/videos/{id}/comment` ; le commentaire créé est renvoyé par le serveur et inséré en tête de liste (l'ordre « pertinence » de YouTube l'enterrerait sinon).
- Sur un direct, la place du panneau est occupée par le chat en direct : le bouton « Commentaires » n'apparaît pas.
- Odysee : lecture seule (les likes/commentaires exigent une signature de portefeuille LBRY) — le champ de saisie est masqué.

---

## 5. Limitations connues

### 5.1 Quota YouTube Data API

- **10 000 unités par jour** (par projet GCP).
- Coûts par action :
  | Action | Coût |
  |--------|------|
  | `videos.rate` (like ou retrait) | 50 unités |
  | `videos.getRating` (état du bouton) | 1 unité |
  | `commentThreads.insert` (commentaire) | 50 unités |
  | `commentThreads.list` (page de commentaires) | 1 unité |

  En pratique, cela permet ~200 likes ou commentaires par jour.

### 5.2 Mode Testing de l'écran de consentement

- Les jetons expirent après **7 jours**. Il suffit de réappuyer sur **L** ou **C** pour relancer le flux OAuth (le navigateur s'ouvre à nouveau).
- Pour une durée de vie permanente, il faut publier l'application (processus de vérification Google) et ajouter des utilisateurs testeurs permanents.

### 5.3 Pas de réponse aux commentaires

- Le like est un vrai bouton bascule côté mobile et navigateur (rating `like` puis `none` pour l'annuler) ; la touche **L** de la TUI ne fait qu'ajouter le like.
- Les réponses aux commentaires (reply) ne sont pas implémentées : seuls les commentaires de premier niveau sont listés et publiés.
- La liste des commentaires YouTube passe aussi par le compte connecté (`commentThreads.list`, 1 unité de quota par page de 50) : sans jeton sur le serveur, les clients affichent « Compte YouTube non connecté ».

### 5.4 Commentaires désactivés

- Certains créateurs désactivent les commentaires sur leurs vidéos → l'API renvoie une erreur 403.

---

## 6. Dépannage

### 6.1 `AuthError: client secret not found`

```
OAuth client secret not found: ~/.config/ytui/client_secret.json
Download it from Google Cloud Console (OAuth client, type 'Desktop app').
```

**Solution** : téléchargez le fichier JSON depuis la GCP Console et placez-le à l'emplacement indiqué (ou mettez à jour `client_secret` dans `[auth]` de `config.toml`).

### 6.2 Token révoqué / expiré

Si le refresh rate une erreur, ytui relance automatiquement le flux OAuth (ouvre le navigateur). C'est fréquent en mode *Testing* (expiration 7 jours).

### 6.3 Erreur 403 — quota dépassé ou action non autorisée

Lors d'un like ou commentaire, si l'API renvoie un code 403, le message affiché est :

```
YouTube refused the request (quota exceeded or action not allowed): ...
```

**Causes possibles** :
- Quota quotidien épuisé → attendre le lendemain.
- Commentaires désactivés sur la vidéo.
- L'application n'est pas autorisée pour l'utilisateur (problème de consentement).

### 6.4 ImportError — bibliothèques Google manquantes

```
Google API libraries are missing. Install them with: pip install 'ytui[auth]'
```

**Solution** : installez les dépendances optionnelles :

```sh
pip install 'ytui[auth]'
```

---

## Résumé des chements de fichiers

| Fichier | Emplacement par défaut | Description |
|---------|----------------------|-------------|
| `config.toml` | `~/.config/ytui/config.toml` | Configuration ytui (inclut `[auth].client_secret`) |
| `client_secret.json` | `~/.config/ytui/client_secret.json` (ou chemin custom) | Secret OAuth Google |
| `oauth_token.json` | `~/.config/ytui/oauth_token.json` | Jeton OAuth2 (chmod 600) |
