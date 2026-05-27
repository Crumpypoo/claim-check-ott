# claim tracker

## How it works

A GitHub Actions workflow runs every day at 8:00 AM UTC. It:
1. Fetches all current claims from the map API
2. Compares them to the previous day's snapshot
3. Logs any changes to `data/changes.md`
4. Optionally posts a summary to Discord
5. Commits the updated snapshot back to this repo

## Files

- `check_claims.py` — the main script
- `data/snapshot.json` — the most recent claim list (auto-updated daily)
- `data/changes.md` — a running log of all changes, newest first
- `.github/workflows/daily.yml` — the Actions schedule

## Setup

1. Fork or create a new repo with these files
2. Push to GitHub
3. *(Optional)* Add a Discord webhook:
   - Go to your Discord server → channel settings → Integrations → Webhooks → New Webhook → copy the URL
   - In your GitHub repo go to Settings → Secrets and variables → Actions → New repository secret
   - Name: `DISCORD_WEBHOOK`, value: the URL you copied
4. Go to the Actions tab and run the workflow manually once to set the initial baseline
5. It will run automatically every day after that

## Changing the schedule

Edit the `cron` line in `.github/workflows/daily.yml`:

```yaml
- cron: '0 8 * * *'   # 8:00 AM UTC daily
```

Some examples:
- `'0 6 * * *'` — 6:00 AM UTC
- `'0 20 * * *'` — 8:00 PM UTC
- `'0 8 * * 1'` — 8:00 AM UTC, Mondays only
