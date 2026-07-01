# Olga Job Search Bot — Active Search

Manual GitHub Actions bot for Product/UX job search.

It reads job sources, filters locally, scores selected vacancies with Claude, sends one Telegram report, and stores history so repeated runs do not resend the same jobs.

## Sources

Current sources:

- RemoteOK RSS
- We Work Remotely RSS
- Remotive RSS
- Greenhouse public job boards
- Lever postings API
- Ashby public job posting API

Not included:

- LinkedIn
- Russian-speaking company Airtable list
- Welcome to the Jungle
- Cover letter generation
- CV analysis

## GitHub Secrets

Repository → Settings → Secrets and variables → Actions → New repository secret:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ANTHROPIC_API_KEY`

## Optional variables

You can add repository variables later:

- `MAX_AI_ANALYSIS_PER_RUN` default `15`
- `MAX_RESULTS_TO_SEND` default `5`
- `MIN_INTERVIEW_CHANCE_TO_SEND` default `55`

## Manual run

Actions → Job search bot → Run workflow.

No schedule is configured.

## Feedback

After receiving a report in Telegram, reply to the bot:

- `👍 abc123`
- `👎 abc123`

Where `abc123` is the job ID shown in the report.

Feedback is processed on the next manual run.

## Data persistence

The workflow commits changes to:

- `data/seen_jobs.json`
- `data/feedback.json`
- `data/telegram_offset.json`

This keeps history across GitHub Actions runs.

If GitHub fails to commit data, go to:

Repository → Settings → Actions → General → Workflow permissions → Read and write permissions.
