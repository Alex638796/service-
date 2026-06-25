# Telegram Forward Bot

A private owner-only Telegram bot that forwards messages from a source channel into a destination forum topic or normal group. Built with aiogram v3, MongoDB Atlas, and deployed on Render.

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

## Render Deployment

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

The bot uses long polling, so no public URL or webhook configuration is needed.

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
├── .env.example
├── handlers/
│   ├── private.py           # All private chat commands and FSM flows
│   └── group.py             # /setdestination in group context
├── services/
│   ├── forwarding.py        # Core forwarding engine
│   └── task_manager.py      # asyncio task lifecycle
├── keyboards/
│   └── main_menu.py         # Inline keyboard layouts
├── models/
│   └── config_model.py      # MongoDB document schemas
└── utils/
    ├── auth.py              # Owner authorization
    └── helpers.py           # DB helpers, message ID extraction, formatting
```
