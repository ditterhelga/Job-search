# Free GitHub Actions Job Bot

This version does not need Render/Railway.
It runs on a GitHub Actions schedule, checks RSS feeds, sends suitable jobs to Telegram, saves `seen_jobs.json`, and exits.

## Required GitHub Secrets

Repository → Settings → Secrets and variables → Actions → New repository secret:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional:

- `ANTHROPIC_API_KEY`

If `ANTHROPIC_API_KEY` is not set, the bot uses a free keyword-based filter instead of Claude.

## Files

Put these files in the root of your repo:

- `bot.py`
- `requirements.txt`
- `.github/workflows/job-bot.yml`

Then go to Actions → Job search bot → Run workflow.
After that it will run every 4 hours.
