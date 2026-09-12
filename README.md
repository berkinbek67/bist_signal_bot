# Crypto Signal Bot (GitHub Actions version)

Checks for an EMA crossover signal (with an RSI filter) every 15 minutes
and messages you on Telegram when one fires. Runs entirely on GitHub's
free servers — nothing runs on your own computer, so local network
issues (proxies, ISPs, antivirus, etc.) don't affect it.

**This bot only sends alerts. It never places trades.**

## 1. Create your Telegram bot (if you haven't already)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
2. Copy the token it gives you (looks like `123456789:ABC...`).
3. Message **@userinfobot** in Telegram — it replies with your numeric chat ID.

## 2. Create a GitHub account and repository

1. Go to https://github.com and sign up (free) if you don't have an account.
2. Click the **+** icon (top right) → **New repository**.
3. Name it anything, e.g. `crypto-signal-bot`. Keep it **Public** (this
   gives unlimited free Actions minutes). Click **Create repository**.

## 3. Upload the files

1. On your new repo's page, click **Add file → Upload files**.
2. Drag in: `main.py`, `check_once.py`, `config.py`, `requirements.txt`.
3. For the workflow file: click **Add file → Create new file**, name it
   exactly `.github/workflows/signal-check.yml` (typing the slashes creates
   the folders automatically), then paste in the workflow file's contents.
4. Click **Commit changes** at the bottom for each upload.

## 4. Add your secrets

Your bot token and chat ID should NOT be typed directly into any file —
GitHub has a secure place for them:

1. In your repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `TELEGRAM_BOT_TOKEN`, Value: your real token. Click **Add secret**.
4. Repeat for `TELEGRAM_CHAT_ID` with your numeric chat ID.

## 5. Turn it on

1. Go to the **Actions** tab of your repo.
2. If prompted, click **"I understand my workflows, go ahead and enable them"**.
3. You should see "Crypto Signal Check" listed. Click it, then click
   **Run workflow** (top right) to trigger it manually the first time and
   confirm it works.
4. Click into that run and check the logs — you should see the same status
   line as before (price, RSI, signal), with no errors.

From here, it runs automatically every 15 minutes, forever, for free —
no computer needs to stay on.

## Changing settings

Open `config.py` in your repo (click it, then the pencil/edit icon) to
change `COIN_ID` (e.g. `ethereum`, `solana`) or strategy parameters like
`EMA_FAST`, `EMA_SLOW`, `RSI_PERIOD`. Commit the change and it takes
effect on the next scheduled run.

## The strategy (what it actually checks)

- **EMA crossover**: fast EMA (default 9-period) crossing above/below the
  slow EMA (default 21-period) signals a possible trend change.
- **RSI filter**: only fires BUY if RSI is under 70, only fires SELL if
  RSI is over 30 — avoids signaling on an already-exhausted move.

This is a simple, transparent starting strategy, not a proven
money-maker. Treat every signal as something to evaluate, not blindly
follow — backtesting against a longer price history is worth doing
before trusting this with real money.
