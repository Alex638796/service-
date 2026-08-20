# Telegram Forward Bot

A private owner-only Telegram bot that forwards messages from a source channel into a destination forum topic or normal group. Built with aiogram v3 and MongoDB Atlas.

> 🚀 **This bot can be deployed on Render, Heroku, Koyeb, Railway, Google Cloud Run, Google Colab, VPS, and Termux.** See [Deployment](#-deployment).

---

## Table of Contents

- [Features](#features)
- [Environment Variables](#environment-variables)
- [GitHub Setup](#github-setup)
- [Deployment](#-deployment)
- [MongoDB Atlas Setup Notes](#mongodb-atlas-setup-notes)
- [Bot Setup Flow](#bot-setup-flow)
- [Range Forwarding](#range-forwarding)
- [Commands Reference](#commands-reference)
- [After a Render Restart](#after-a-render-restart)
- [Project Structure](#project-structure)

---

## Features

- Forward videos, documents (PDF, HTML), text messages, and photos
- Forum topic forwarding with automatic `message_thread_id` detection
- Normal group forwarding
- Range forwarding: select start/end messages by forwarding them to the bot
- Configurable per-message delay
- FloodWait handling with automatic retry
- Progress updates during forwarding
- Checkpoint-based resume after Render restarts
- Owner-only access
- Per-user keep-alive self-pinger while a forwarding job is active (auto-detects Render's `RENDER_EXTERNAL_URL`; harmlessly disables itself on platforms without a public URL, e.g. Google Colab)

---

## Environment Variables

Set these in Render Dashboard → Environment:

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `OWNER_ID` | ✅ | Your Telegram user ID |
| `MONGO_URI` | ✅ | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | optional | Database name (default: `tgforwardbot`) |
| `DEFAULT_DELAY_SECONDS` | optional | Delay per message (default: `3.0`) |

To get your `OWNER_ID`, message [@userinfobot](https://t.me/userinfobot) on Telegram.

---

## GitHub Setup

1. Create a new repository on GitHub (private recommended)
2. Clone it locally:
   ```bash
   git clone https://github.com/yourusername/your-repo-name.git
   cd your-repo-name
   ```
3. Copy all project files into the repository folder
4. Push to GitHub:
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

---

## 🚀 Deployment

This bot uses long polling (not a Telegram webhook), so it doesn't strictly need a public URL to function — the included aiohttp server exists only as a health-check endpoint for platforms (like Render's free tier) that require the process to bind a port. It supports **Render, Heroku, Koyeb, Railway, Google Cloud Run, Google Colab, VPS, and Termux**.

### One-Click Deploy

| Platform | Deploy |
|---|---|
| Render | [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Alex638796/service) |
| Heroku | [![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Alex638796/service) |
| Koyeb | [![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=github.com/Alex638796/service&branch=main&name=tg-forward-bot) |
| Google Colab | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Alex638796/service/blob/main/colab_deploy.ipynb) |
| Google Cloud | [![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://ssh.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/Alex638796/service&cloudshell_tutorial=.cloudshell/GCLOUD.md) |

> ⚠️ None of these badges fully automate deployment — each opens that platform's setup screen where you still need to fill in environment variables manually (see the [Environment Variables](#environment-variables) table above). They save the "find and configure a new app" step, not the "enter your credentials" step.

> ℹ️ **Railway**: this bot can also be deployed on Railway — it auto-detects the Python app via Nixpacks and picks up `requirements.txt` + `Procfile` with no extra configuration needed. There's no one-click badge here because Railway deploy buttons require a pre-registered Railway template (a manual one-time setup on Railway's side, separate from this repo). To deploy: create a new Railway project → "Deploy from GitHub repo" → select this repo → set the environment variables from the table above.

### Render (primary supported platform)

1. Log in to [Render](https://render.com)
2. Click **New → Web Service**
3. Connect your GitHub repository
4. Render will detect `render.yaml` automatically. If not, configure manually:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Plan:** Free
5. Add environment variables in the **Environment** tab:
   - `BOT_TOKEN`
   - `OWNER_ID`
   - `MONGO_URI`
6. Click **Deploy**

The bot uses long polling, so no public URL or webhook configuration is needed — Render's free tier just requires the process to bind `$PORT`, which the built-in health server already handles.

### Heroku

Uses the included `app.json` and `Procfile`. After clicking the badge above, fill in the prompted fields (`BOT_TOKEN`, `OWNER_ID`, `MONGO_URI`).

### Koyeb / Google Cloud Run

Both use the included `Dockerfile` directly — no extra build configuration needed. For manual Cloud Run deployment via `gcloud` CLI, see `.cloudshell/GCLOUD.md`.

### Google Colab (temporary/testing)

Click the Colab badge above to open `colab_deploy.ipynb`. Fill in the mandatory fields (`BOT_TOKEN`, `OWNER_ID`, `MONGO_URI`) — optional fields (`MONGO_DB_NAME`, `DEFAULT_DELAY_SECONDS`) come pre-filled — and run the single cell. It clones the repo, installs dependencies, and runs `python3 main.py`. Keep-alive pinging is automatically inactive here since Colab has no `RENDER_EXTERNAL_URL`. The cell blocks and streams logs live; press ■ to stop.

> ⚠️ Colab sessions are temporary (disconnect on tab close, inactivity, or after Colab's free-tier time limit — up to ~12 hours). Use this for quick testing only; for always-on hosting, use Render/Heroku/Koyeb/Railway above.

### VPS

```bash
git clone https://github.com/Alex638796/service.git
cd service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="your_bot_token"
export OWNER_ID="your_telegram_user_id"
export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net"
python3 main.py
```

Or via Docker, using the included `Dockerfile`:

```bash
git clone https://github.com/Alex638796/service.git
cd service
sudo apt install docker.io -y
sudo docker build -t tg-forward-bot .
sudo docker run -it --rm --env-file .env tg-forward-bot
```

No public URL is required — this bot works over long polling on any VPS with outbound internet access.

### Termux (Android)

```bash
pkg update && pkg upgrade -y
pkg install python git -y
git clone https://github.com/Alex638796/service.git
cd service
pip install -r requirements.txt
export BOT_TOKEN="your_bot_token"
export OWNER_ID="your_telegram_user_id"
export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net"
python3 main.py
```

> If any MongoDB-related package fails to build on Termux, run `pkg install libffi openssl` first, then retry `pip install -r requirements.txt`. A remote MongoDB instance (e.g. MongoDB Atlas's free tier) is recommended over trying to run MongoDB on-device.

---

## MongoDB Atlas Setup Notes

Your Atlas connection string must allow connections from all IPs (`0.0.0.0/0`) in **Network Access**, because Render free tier uses dynamic IPs.

The bot will create all required collections automatically on first use.

---

## Bot Setup Flow

### Step 1: Add the bot to your source channel
- Open the source channel settings
- Add the bot as admin with at least **Post Messages** permission

### Step 2: Add the bot to your destination group/supergroup
- Open the group settings
- Add the bot as admin with at least **Send Messages** permission

### Step 3: Configure source channel
In the bot's private chat:
```
/setsource
```
Then forward any message from the source channel to the bot.

### Step 4a: Configure destination — Forum Topic
In the bot's private chat:
```
/arm_topic_mode
```
Then go to the destination supergroup, open the target topic, and send:
```
/setdestination
```

### Step 4b: Configure destination — Normal Group
In the bot's private chat, press **Set Normal Group** or:
```
/arm_topic_mode
```
Then go to the normal group and send:
```
/setdestination
```

---

## Range Forwarding

1. In bot private chat, press **Range Forward** or type `/range`
2. Forward the **first** message of your desired range from the source channel
3. Forward the **last** message of your desired range from the source channel
4. Confirm the range — forwarding starts immediately
5. The bot sends progress updates every 25 messages
6. When complete, the bot sends a summary

To stop mid-forwarding:
```
/stop
```

---

## Commands Reference

| Command | Description |
|---|---|
| `/start` | Open main menu |
| `/menu` | Open main menu |
| `/setsource` | Configure source channel |
| `/arm_topic_mode` | Arm topic/group capture mode |
| `/range` | Start range forwarding |
| `/stop` | Stop active forwarding |
| `/status` | Show current configuration and status |
| `/setdelay` | Change forwarding delay |

The `/setdestination` command is sent **inside the destination group/topic**, not in private chat.

---

## After a Render Restart

If Render restarts the bot while forwarding is active, the bot will:
1. Detect the interrupted task on startup
2. Send you a message with the last processed message ID
3. Tell you the exact start point to resume from

To resume, use `/range` and forward the next message as the new start.

---

## Project Structure

```
├── main.py                  # Entry point
├── config.py                # Environment variables
├── database.py              # MongoDB connection
├── requirements.txt
├── render.yaml
├── Procfile                 # Heroku process definition
├── Dockerfile                # Used by Koyeb, Railway, Google Cloud Run, VPS-via-Docker
├── app.json                 # Heroku one-click deploy manifest
├── colab_deploy.ipynb        # Google Colab one-click deploy notebook
├── .cloudshell/              # Google Cloud Shell walkthrough
│   ├── tutorial.yaml
│   └── GCLOUD.md
├── .env.example
├── handlers/
│   ├── private.py           # All private chat commands and FSM flows
│   └── group.py             # /setdestination in group context
├── services/
│   ├── forwarding.py        # Core forwarding engine
│   ├── keepalive.py         # Per-user keep-alive self-pinger
│   └── task_manager.py      # asyncio task lifecycle
├── keyboards/
│   └── main_menu.py         # Inline keyboard layouts
├── models/
│   └── config_model.py      # MongoDB document schemas
└── utils/
    ├── auth.py              # Owner authorization
    └── helpers.py           # DB helpers, message ID extraction, formatting
```
