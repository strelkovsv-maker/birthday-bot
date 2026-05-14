# Birthday Bot

A Telegram bot that posts personalized Russian birthday wishes for your team.
Each morning it checks a Google Sheet for today's birthdays, asks Claude
Sonnet to write a warm, smart wish (with optional references to AI / finance /
risk-management topics), DMs the draft to you for approval, and posts it to
the team group only after you tap **✅ Опубликовать**.

If you don't like the draft, tap **🔄 Новый вариант** and the bot generates a
new one — feeding the rejected versions back into the model so it doesn't
repeat itself.

---

## Architecture at a glance

```
                       ┌─────────────────────────────┐
                       │       Railway (cloud)       │
                       │  ┌────────────────────────┐ │
                       │  │   birthday-bot (24/7)  │ │
                       │  │  ─ daily 10:00 trigger │ │
                       │  │  ─ telegram polling    │ │
                       │  │  ─ sqlite state file   │ │
                       │  └────┬───────┬───────┬───┘ │
                       └───────┼───────┼───────┼─────┘
                               │       │       │
                 ┌─────────────┘       │       └──────────────┐
                 ▼                     ▼                      ▼
       ┌─────────────────┐  ┌────────────────────┐  ┌──────────────────┐
       │  Google Sheets  │  │  Anthropic API     │  │  Telegram API    │
       │  (team data)    │  │  (Claude Sonnet)   │  │  (DMs + group)   │
       └─────────────────┘  └────────────────────┘  └──────────────────┘
```

---

## What you need before starting

- A [Telegram](https://telegram.org/) account, plus access to your team's group chat
- An [Anthropic Console](https://console.anthropic.com) account with billing
  set up and an API key (`sk-ant-api03-…`)
- A [Google account](https://accounts.google.com) (for the sheet + Google Cloud)
- A [GitHub](https://github.com) account (for hosting the code)
- A [Railway](https://railway.app) account (for running the bot 24/7; free tier works)
- Python 3.11+ on your local machine, just for one-time setup helpers

Total cost: ≈ $5 in prepaid Anthropic credits (lasts years), Railway is free.

---

## Setup walkthrough

Steps are in order. Each one builds on the previous.

### 1. Create the Telegram bot

1. In Telegram, message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`. Pick a display name (e.g. `Team Birthday Bot`) and a
   username ending in `bot` (e.g. `team_birthday_bot`)
3. BotFather replies with an HTTP API token like `123456:AAA…`. **Save it** —
   this is `TELEGRAM_BOT_TOKEN`.
4. Still in BotFather, send `/setprivacy` → choose your bot → **Disable**.
   This lets the bot see all messages in groups it's added to (needed for
   the `get_group_id.py` helper to work without forcing you to use commands).

### 2. Add the bot to your team group

1. Open your team group in Telegram → group settings → Members → Add Member
2. Search for your bot's username → add it
3. Promote it to **admin** (right-click / long-press → Manage → Promote).
   Admin rights aren't strictly required for posting, but they make things
   smoother and let the bot tag people reliably.

### 3. Set up your local development environment

You only need this for the helper scripts and the local test run.

```bash
cd "/Users/ruasesk/Documents/Claude_TEST/TG BOT/birthday-bot"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` in a text editor and paste your `TELEGRAM_BOT_TOKEN`. Leave the
others blank for now — we'll fill them as we go.

### 4. Get your personal chat_id

The bot DMs you for approval, so it needs to know your chat_id.

```bash
python -m tools.get_my_chat_id
```

The script starts your bot. Open Telegram, find the bot, send it any
message (e.g. `/start`). The script prints your chat_id. Copy it into
`OWNER_CHAT_ID` in `.env`. Stop the script with `Ctrl+C`.

### 5. Get the team group's chat_id

```bash
python -m tools.get_group_id
```

In your team group, send `/id`. The script prints the group's chat_id
(typically a negative number like `-1001234567890`). Copy it into
`GROUP_CHAT_ID` in `.env`. Stop the script.

### 6. Set up Google Sheets access

This is the longest step. You're creating a "service account" — a robot
identity Google APIs accept — and giving it read access to one specific sheet.

**6a. Create a Google Cloud project**

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Top bar → project picker → **New Project**. Name it e.g.
   `birthday-bot-sheets`. No organization needed if you don't have one.
3. Wait ~30 seconds for it to provision

**6b. Enable the Sheets API**

1. Search bar at top → "Google Sheets API" → click the result → **Enable**
2. (Also enable "Google Drive API" if prompted — gspread uses both)

**6c. Create a service account**

1. Left menu → **IAM & Admin** → **Service Accounts** → **+ Create Service Account**
2. Name: `birthday-bot-reader`. Click **Create and Continue**.
3. Role: skip (don't assign anything). Click **Continue** → **Done**.
4. Click into the new service account → **Keys** tab → **Add Key** → **JSON**.
   A `.json` file downloads. Keep it safe — this is the credential.
5. Note the service account's email — looks like
   `birthday-bot-reader@your-project.iam.gserviceaccount.com`. You'll need it
   in step 6e.

**6d. Create the Google Sheet**

1. [sheets.google.com](https://sheets.google.com) → blank sheet
2. Name it (e.g. `Team Birthdays`)
3. Set up the header row exactly:

   | A: Name | B: Telegram Handle | C: Birthday | D: Department | E: Role | F: Notes |
   |---------|--------------------|-------------|---------------|---------|----------|

4. Add your team members. Examples:

   | Name             | Telegram Handle | Birthday   | Department         | Role                                                              | Notes                                       |
   |------------------|-----------------|------------|--------------------|-------------------------------------------------------------------|---------------------------------------------|
   | Иванова Анна     | @anna_iv        | 1990-07-15 | Market Risk        | главный аналитик по управлению рисками торговой книги             | любит горный велосипед, FRM, кофеман       |
   | Смирнов Павел    | @pavel_s        | 1988-04-29 | FMRM IT Core       | менеджер по развитию бизнес-технологий, вице-президент            | недавно пробежал марафон, фанат джаза      |

   `Department` should be one of: **Market Risk**, **Liquidity Risk**, **FI Risk**,
   **Trading Infrastructure**, **Price Competence Center**, **FMRM IT Core**,
   **Quantitative modeling**. The bot's prompt knows these categories and
   tailors topical references accordingly. Note: **Banking Book** is part of
   Market Risk in this taxonomy.

   `Role` is the person's actual job title in Russian (free text) — the LLM
   uses it as a primary source of personalization.

   `Notes` is free-text personal context (hobbies, recent achievements,
   in-jokes). Optional but makes wishes much warmer.

   **Birthday format is strict: MONTH ALWAYS COMES FIRST.** Accepted:

   - `YYYY-MM-DD` — recommended, e.g. `1990-05-10` means May 10
   - `MM-DD-YYYY` — e.g. `05-10-1990`
   - `MM-DD`, `MM/DD`, `MM.DD` — year-less, e.g. `05-10` means May 10

   Year is ignored — only month + day are used for matching today.

   ⚠️ **Google Sheets locale gotcha**: if you paste birthdays in like
   `05-10`, Google Sheets may auto-interpret them as dates using your
   account's locale (Russian users often see `05-10` get re-displayed
   as `10.05` because Sheets read it as DD-MM). To prevent this:

   1. Open your Google Sheet
   2. Select the entire `Birthday` column (column C)
   3. Format menu → **Number** → **Plain text**
   4. Now paste your birthdays in `MM-DD` or `YYYY-MM-DD` format —
      they'll stay as text, no auto-conversion

   If a birthday starts with a number > 12 (e.g. `13-05`), the bot logs
   a warning and skips that row — that's how you'll catch any cells that
   accidentally ended up as day-first.

5. Copy the **sheet ID** from the URL — it's the long string between
   `/d/` and `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbC…XYZ`**`/edit`
   That's `GOOGLE_SHEET_ID`.

6. Note the tab name (default is "Sheet1" / "Лист1"). Rename it to `Team`
   to match the default in `.env.example`, or update `GOOGLE_SHEET_TAB`
   in `.env` to match whatever it's called.

**6e. Share the sheet with the service account**

1. In the sheet, click **Share** (top right)
2. Paste the service account's email (from 6c step 5) → set permission to
   **Viewer** → uncheck "Notify people" → **Share**

**6f. Put the credentials into `.env`**

Open the JSON file you downloaded in 6c. Two options:

- **Recommended for Railway:** copy the entire JSON contents (including the
  outer `{ }` braces) and paste as a single value into
  `GOOGLE_SERVICE_ACCOUNT_JSON=`. The shell-quoting can be fiddly locally;
  for `.env`, just put it on one line with no surrounding quotes.
- **For local-only convenience:** save the JSON file somewhere safe and put
  the absolute path into `GOOGLE_SERVICE_ACCOUNT_JSON=` (the bot detects
  whether it's JSON content or a path).

Also fill in `GOOGLE_SHEET_ID`.

### 7. Add your Anthropic key

You already have it. Paste it into `ANTHROPIC_API_KEY=` in `.env`.

### 8. Local smoke test

```bash
# Sanity-check the LLM + prompt
python -m tools.preview_wish \
  --name "Анна Петрова" \
  --department "Market Risk" \
  --notes "PM, любит горный велосипед, недавно сдала FRM, кофеман" \
  --retries 1
```

You should see two distinct birthday wishes printed. If the tone feels off,
edit `src/prompts.py` and re-run. Cheap and fast.

```bash
# DRY_RUN end-to-end test (DM works, but no actual group post on approve)
DRY_RUN=true python -m src.main
```

In Telegram, message your bot `/test`. It should:
- Read your Google Sheet
- Find any matches for today (likely none, unless someone really has a birthday)
- DM you with drafts (if matches found) or do nothing (if not)

To force a draft for testing, temporarily change one row's birthday to
today's date in the sheet, then `/test` again. After approving, the bot logs
"DRY_RUN: would have posted to group: …" but doesn't actually post.

Stop with `Ctrl+C` when done.

### 9. Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"

# Create a private repo on github.com first, then:
git remote add origin git@github.com:<your-username>/birthday-bot.git
git push -u origin main
```

**Make absolutely sure `.env` is gitignored.** It is by default in this
project, but double-check with `git status` before committing — if you see
`.env` listed, stop and fix `.gitignore` first.

### 10. Deploy to Railway

1. [railway.app](https://railway.app) → Sign in with GitHub → **New Project** →
   **Deploy from GitHub repo** → pick your repo
2. Railway auto-detects the `Dockerfile` and starts building. The first
   build takes 2–3 minutes.
3. Open the project → **Variables** tab. Add each of these (paste the values
   from your local `.env`):
   - `TELEGRAM_BOT_TOKEN`
   - `ANTHROPIC_API_KEY`
   - `ANTHROPIC_MODEL` (set to `claude-sonnet-4-6`)
   - `OWNER_CHAT_ID`
   - `GROUP_CHAT_ID`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_SHEET_TAB` (set to `Team`)
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the **full JSON content** as the
     value. Railway accepts multi-line values fine.
   - `TIMEZONE` (set to `Europe/Moscow`)
   - `SEND_TIME` (set to `10:00`)
   - `DRY_RUN` — leave as `false` once you're ready to go live
4. Add a **persistent volume** so the SQLite state file survives restarts:
   - Project → **Settings** → **Volumes** → **+ New Volume**
   - Mount path: `/data`
   - Size: 1 GB is plenty
5. Set `DB_PATH=/data/state.db` in Variables (so SQLite writes to the volume)
6. Click **Deploy**. Watch the logs — you should see:
   ```
   Starting birthday-bot — timezone=Europe/Moscow, send_time=10:00, dry_run=False, model=claude-sonnet-4-6
   Scheduler started: daily check at 10:00 Europe/Moscow
   Bot up and polling.
   ```

### 11. Live verification

1. In your team Google Sheet, temporarily change your own row's birthday to
   today's date
2. In Telegram, DM the bot `/test`
3. Within a few seconds you should get a DM with the draft and three buttons
4. Tap **🔄 Новый вариант** to confirm regeneration works
5. Tap **✅ Опубликовать** to confirm it posts to the group
6. **Change your row in the sheet back** to your real birthday

You're done. Tomorrow morning at 10:00 Moscow, the cron will fire automatically.

---

## Day-to-day usage

- **Add or remove team members:** edit the Google Sheet directly. The bot
  reads it fresh every morning.
- **Update someone's notes:** same — edit the sheet, the bot picks up changes
  on the next run.
- **Manually trigger today's check:** DM the bot `/test`. Useful if you want
  to verify nothing is broken, or if you set the bot up after 10:00.
- **Tweak the tone:** edit `src/prompts.py` and push. Railway auto-redeploys.

---

## Troubleshooting

**The bot never DMs me.**
- Did you message the bot at least once first? Bots can't DM users out of the
  blue.
- Is `OWNER_CHAT_ID` correct? Re-run `tools/get_my_chat_id.py` to verify.
- Check Railway logs for errors.

**The bot doesn't post to the group when I approve.**
- Make sure the bot is added to the group **and is admin** (or at least has
  permission to send messages — some groups restrict this for non-admins).
- Verify `GROUP_CHAT_ID` is correct (negative number for groups/supergroups).

**"Sheet is missing required column(s)" error.**
- Headers must include at least `Name` and `Birthday`. Check the first row
  of your sheet.

**"Unauthorized" / 401 from Anthropic in the logs.**
- Billing isn't set up, or the key was revoked. Visit
  [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing)
  and confirm you have credits or a payment method.

**Daily check didn't fire at 10:00.**
- Check Railway logs for crash/restart messages. The bot retries on failure
  with `restartPolicyType: ON_FAILURE`.
- Verify `TIMEZONE` is set correctly. APScheduler is timezone-aware, so
  `SEND_TIME=10:00` + `TIMEZONE=Europe/Moscow` means 10:00 *Moscow*, not UTC.

**Drafts feel repetitive across people.**
- Tweak `src/prompts.py` — increase the "vary заходы" emphasis, add more
  variety guidance, or raise temperature in `src/llm.py` (currently 0.85).

**I want to roll back to a previous version of a wish.**
- The bot stores prior drafts in SQLite. They're not user-facing, but you
  can `sqlite3 /data/state.db "SELECT current_draft, prior_drafts_json FROM drafts WHERE id = N"`
  to retrieve them if needed.

---

## Project layout

```
birthday-bot/
├── src/
│   ├── main.py            # Bootstrap: wires deps, starts polling + scheduler
│   ├── scheduler.py       # APScheduler: daily check job
│   ├── sheets.py          # Google Sheets reader + birthday parser
│   ├── llm.py             # Anthropic client wrapper
│   ├── telegram_bot.py    # Inline-keyboard approval flow
│   ├── state.py           # SQLite: pending drafts
│   ├── prompts.py         # Russian prompt template (edit here for tone)
│   └── config.py          # Env-var loader
├── tools/
│   ├── get_my_chat_id.py  # One-time helper: print your chat_id
│   ├── get_group_id.py    # One-time helper: print group's chat_id
│   └── preview_wish.py    # Generate a wish locally without sending
├── Dockerfile
├── railway.json
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Cost expectations

- **Railway:** free tier covers this load (~$0/mo). Memory < 100 MB,
  99% idle.
- **Anthropic Claude Sonnet:** ~$0.01–0.03 per generated wish, including
  retries. For a 10-person team that's ~$0.30–1/year.
- **Google Sheets / Telegram:** free.

---

## Security notes

- `.env` is gitignored. Never commit secrets.
- The Anthropic key, Telegram token, and service account JSON live only in
  Railway's secret store once deployed.
- The service account has read-only access to one specific sheet. If the
  key leaks, the worst case is someone reading your team's birthdays.
- The bot only accepts approval-button taps from `OWNER_CHAT_ID` — other
  users tapping the buttons get a polite refusal popup.
