# LinkedIn Content Pipeline

A fully automated Python pipeline that runs every Sunday via GitHub Actions.
It scrapes the top frontend engineering articles, scores them with Claude,
generates a ready-to-post LinkedIn post for each one, saves all 10 to Notion,
and pings you on Telegram.

```
frontendcs.com  →  Claude scoring  →  Jina AI Reader  →  Claude ghostwriter
                                                               ↓
                                                       Notion database
                                                               ↓
                                                     Telegram notification
```

---

## What you need before you start

| Secret | Where it comes from |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `NOTION_API_KEY` | notion.so developer integrations |
| `NOTION_DATABASE_ID` | your Notion database URL |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | your personal or group chat ID |

---

## Step-by-step setup

### 1. Get your Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com) and sign in (or create an account).
2. Click **API Keys** in the left sidebar.
3. Click **Create Key**, give it a name like `linkedin-pipeline`, and copy the key.
4. The key starts with `sk-ant-…` — store it somewhere safe; you cannot view it again.

> The pipeline uses `claude-sonnet-4-20250514` for both scoring and post generation.
> Make sure your account has credits or an active plan.

---

### 2. Create a Notion integration and get the API key

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations).
2. Click **+ New integration**.
3. Name it `linkedin-pipeline`, select your workspace, and click **Submit**.
4. Copy the **Internal Integration Token** (starts with `secret_…`). This is your `NOTION_API_KEY`.

---

### 3. Create the Notion database with the correct schema

You must create the database manually (the pipeline writes to it but does not create it).

#### 3a. Create a new full-page database in Notion

1. Open Notion → click **+** to add a new page → choose **Table** (full page database).
2. Name it **LinkedIn Posts** (or anything you like).

#### 3b. Add these exact properties

| Property name | Type | Notes |
|---|---|---|
| `Title` | Title | Default — rename if needed |
| `Company` | Text | |
| `URL` | URL | |
| `Score` | Number | |
| `LinkedIn Post` | Text | |
| `Publish Day` | Select | Add options: Mon, Wed, Fri, Mon2, Wed2, Fri2, Mon3, Wed3, Fri3, Mon4 |
| `Week Of` | Date | |
| `Status` | Select | Add options: Draft, Approved, Posted |

#### 3c. Connect your integration to the database

1. Open the database page in Notion.
2. Click **…** (top-right) → **+ Add connections** → search for `linkedin-pipeline` → click **Confirm**.

#### 3d. Get the database ID

The database ID is the 32-character hex string in the page URL:

```
https://www.notion.so/YOUR_WORKSPACE/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                     This is your NOTION_DATABASE_ID
```

Copy it — you will need it in step 5.

---

### 4. Create a Telegram bot and get your chat ID

#### 4a. Create the bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Follow the prompts: choose a display name and a username (must end in `bot`).
4. BotFather replies with your **bot token** — copy it. This is `TELEGRAM_BOT_TOKEN`.

#### 4b. Get your chat ID

**Option A — personal message:**

1. Search for your new bot in Telegram and send it any message (e.g. `/start`).
2. Open this URL in your browser (replace `<TOKEN>` with your token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Find `"chat": {"id": 123456789, …}` — that number is your `TELEGRAM_CHAT_ID`.

**Option B — group chat:**

1. Add your bot to the group.
2. Send a message in the group.
3. Fetch `getUpdates` as above; the group chat ID is a **negative** number (e.g. `-987654321`).

---

### 5. Add all secrets to your GitHub repository

1. Push this project to a new GitHub repository (public or private, both work).
2. Go to the repo → **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret** for each of the five secrets:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-…` |
| `NOTION_API_KEY` | `secret_…` |
| `NOTION_DATABASE_ID` | 32-char hex from your Notion URL |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC…` |
| `TELEGRAM_CHAT_ID` | your numeric chat/user ID |

---

### 6. Trigger a manual test run

Before waiting for Sunday you can run the pipeline immediately:

1. Go to your repo → **Actions** tab.
2. Select **Weekly LinkedIn Content Pipeline** in the left sidebar.
3. Click **Run workflow** → **Run workflow** (green button).
4. Watch the logs — each step prints structured output.
5. Check Notion for 10 new draft rows and your Telegram for the summary message.

---

## How the pipeline works

| Step | What happens |
|---|---|
| 1 — Scrape | Fetches frontendcs.com, parses article entries for the current and previous year, filters out anything older than 6 months, collects up to 40 candidates |
| 2 — Score | Sends title + company to Claude; gets a 1-10 score + one-line reason; sorts descending; keeps top 10 |
| 3 — Fetch | Retrieves full article text via `https://r.jina.ai/<url>` (no API key needed); truncates to 4 000 chars |
| 4 — Generate | Calls Claude with the ghostwriter system prompt + article content; produces a 1 000-1 300 char LinkedIn post |
| 5 — Notion | Creates one page per article in your database; assigns a publish slot (Mon/Wed/Fri over 3.5 weeks) |
| 6 — Telegram | Sends a summary with the top-3 article titles and scores |

---

## Error handling

The pipeline is designed to **never crash mid-run**:

- Jina fetch failure → logs warning, falls back to title + company as context
- Claude scoring failure → logs warning, assigns score 5, continues
- Claude post generation failure → saves `GENERATION_FAILED` string, continues
- Notion save failure → logs error, continues saving remaining posts
- Telegram failure → logs error, does not abort pipeline

At the end, the logs print a full run summary with counts of successes and failures.

---

## File structure

```
linkedin-pipeline/
├── pipeline.py                        # Main script — all 6 steps
├── requirements.txt                   # Python dependencies
├── .github/
│   └── workflows/
│       └── weekly_pipeline.yml        # GitHub Actions cron + manual trigger
└── README.md                          # This file
```

---

## Modifying the content voice

The ghostwriter system prompt lives in `pipeline.py` as the constant
`LINKEDIN_SYSTEM_PROMPT`. Edit it directly to change Anshu's positioning,
target audience, content pillars, style rules, or quality bar.

The Claude model is set via `CLAUDE_MODEL = "claude-sonnet-4-20250514"` near
the top of `pipeline.py`. Update this string to switch models.

---

## Publish schedule

The 10 posts are assigned slots in score order (highest score = first Monday):

```
Rank 1  → Mon     (Week 1)
Rank 2  → Wed     (Week 1)
Rank 3  → Fri     (Week 1)
Rank 4  → Mon2    (Week 2)
Rank 5  → Wed2    (Week 2)
Rank 6  → Fri2    (Week 2)
Rank 7  → Mon3    (Week 3)
Rank 8  → Wed3    (Week 3)
Rank 9  → Fri3    (Week 3)
Rank 10 → Mon4    (Week 4)
```

In Notion, change **Status** from `Draft` → `Approved` → `Posted` as you work
through each week's content.
