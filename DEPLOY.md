# Deploying Fruit Fly TV

The site is one page (`fruit-fly-tv.html`) plus a small Python server (`serve.py`) that checks whether the
channel is live, lists its uploads and fetches the connectome data. The server must run somewhere; a static
host (GitHub Pages, Netlify) is not enough.

Files in this folder:

| File | Used by |
|---|---|
| `Dockerfile`, `.dockerignore` | Render, Railway, Fly.io, any Docker host |
| `render.yaml` | Render (Blueprint) |
| `Procfile` | Railway / Heroku-style hosts without Docker |
| `fly.toml` | Fly.io |
| `start-fly-tv.bat` | running it on your own Windows PC |

## Render (simplest)

1. Put this folder in a GitHub repository.
2. In Render: **New → Blueprint**, choose the repo. It reads `render.yaml` and builds the Dockerfile.
3. When the deploy finishes you get `https://fruit-fly-tv.onrender.com` (name may differ).

The free plan sleeps after 15 minutes without visitors; the first visit afterwards takes ~30 s to wake up
and re-download the brain data (about 10 MB).

## Railway

1. **New Project → Deploy from GitHub repo**, choose the repo. Railway detects the Dockerfile.
2. **Settings → Networking → Generate Domain**.

## Fly.io

```bash
fly launch --copy-config --yes
fly deploy
```

## Any Docker host / your own server

```bash
docker build -t fruit-fly-tv .
docker run -d -p 80:8765 --name fruit-fly-tv fruit-fly-tv
```

## Things to know

- **HTTPS**: Render, Railway and Fly provide it. The YouTube embed works on plain http too, but browsers
  are stricter about autoplay and sound on http.
- **Sound** still starts muted for every visitor until they tap once. Browsers require that.
- **Data-centre IPs**: YouTube sometimes serves a consent/bot page to cloud servers. If the tab title says
  the helper server can't be reached or the TV shows YouTube's plain channel embed, that's what happened.
  Running the server at home (the `.bat`) or routing the checks through a residential proxy avoids it.
- **Environment variables** (all optional): `PORT` (set by the host), `HOST` (bind address), `CACHE_DIR`
  (where the downloaded brain files go; a persistent disk avoids re-downloading after restarts).
- **Changing the channel**: edit the `CHANNEL` line near the top of `fruit-fly-tv.html` and redeploy.
