# Deploy to Google Cloud Run

This bot ships with a `Dockerfile`, so it deploys to Cloud Run with no extra
build configuration.

## 1. Set your project

```sh
gcloud config set project YOUR_PROJECT_ID
```

## 2. Build and deploy

```sh
gcloud run deploy tg-forward-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars \
BOT_TOKEN=your_bot_token,\
OWNER_ID=your_telegram_user_id,\
MONGO_URI=your_mongodb_connection_string,\
MONGO_DB_NAME=tgforwardbot,\
DEFAULT_DELAY_SECONDS=3.0
```

Cloud Run will print a service URL when this finishes (e.g.
`https://tg-forward-bot-xxxxx.a.run.app`). This bot doesn't need that URL for
core functionality (it's polling-based, not webhook-based) — Render-style
keepalive auto-detection specifically looks for `RENDER_EXTERNAL_URL`, so
keep-alive pinging is simply inactive on Cloud Run unless you wire that up
separately.

## 3. Verify

```sh
gcloud run services describe tg-forward-bot --region us-central1
```

Visit the printed service URL's `/health` path — it should return `OK`.

See `render.yaml` or `config.py` in the repo root for what each variable
means and how to obtain it (BotFather, userinfobot, MongoDB Atlas, etc).
