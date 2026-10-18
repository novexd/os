# Independent HidenCloud RSS worker

This worker runs as a **separate Python process** on the same HidenCloud server as Prowl. It does not import or change Prowl. No third-party Python packages are required.

## Prepare

1. Place `rss_worker.py` in a separate directory (for example `/home/container/rss-relay/`) alongside a **copy** of `last_seen.json` from this repository. Do not use Prowl's data directory for the history file.
2. Set `NERIMITY_WEBHOOK` as an environment variable; do not put it in source code. You may set `RSS_STATE_FILE` to a writable absolute path if needed.
3. Start with `python3 -u rss_worker.py`. The worker polls Reddit every 120 seconds and X feeds every 300 seconds by default.
4. **Only after verifying it runs and sends updates**, disable the GitHub Actions schedule in `.github/workflows/rss.yml` to avoid duplicate notifications.

## Important deployment note

The HidenCloud server's existing startup command must still start Prowl. A second process can be launched with a supervised shell script, but its exact launcher depends on the current server startup configuration. Do **not** replace Prowl's startup command with `python3 rss_worker.py`.

The worker uses a single `last_seen.json` checkpoint, written atomically. It assumes only one worker instance is running and its working directory/state path persists across server restarts. A webhook success followed by a crash before checkpoint persistence can occasionally produce a duplicate message.

## Known limits

- Reddit's RSS feed works but does not expose actual post flair. The title-based Gameplay filter is a heuristic and can miss or misclassify posts.
- Keep.md's X feeds can be stale, and `@osu_SOTD` has returned 404. Polling more often cannot fix upstream feed availability.
- An unreachable feed is logged without stopping other feeds.
