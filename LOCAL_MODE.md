# Local mode (no Upstash needed) -- Oct 6 2026

Use this when Upstash is unavailable (limit hit, no money). Everything runs on
your PC; state lives in `.gem_alert_state.json` in the repo folder. Safe for
several programs at once (file lock + atomic writes; tested with 4 processes).

## Turn it on (CMD, in the repo folder)
1. Edit `.env`: **delete or blank** `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`. Keep every other key.
2. Run `start_local_mode.bat` -- it pulls the latest code and starts three minimized windows:
   fast watcher, cycle supervisor (`local_runner.py`: poll-fast every 10 min, poll-slow every 20, MadeOnSol every 15), dashboard on http://localhost:8787.
3. In Windows Task Scheduler, disable the old "poll-madeonsol" job (the supervisor now runs it) -- or set `LOCAL_RUNNER_MADEONSOL=false` in `.env`.
4. GitHub runners need nothing: with no Upstash secrets they exit immediately ("SKIPPED ... local mode"). Delete the two Upstash Secrets or leave them -- if they still point at the exhausted DB the runners stay blocked and harmless.

## Honest limits
- The PC must stay on and online. If it sleeps, cycles pause (the next one catches up on restart).
- Old data in Upstash is NOT copied over (it can't be read while blocked). Local mode starts fresh: open positions held in the old DB are not tracked here -- check the wallet by hand for anything still open.
- GitHub-hosted cycles are off, so the `poll-fast` / `poll-slow` cron-job.org dispatches do nothing useful until Upstash is back.

## Go back to Upstash later
Put the two Upstash values back in `.env` and the two GitHub Secrets, run `update.bat`. State does not transfer automatically; local data stays in the file.

## Tuning
`LOCAL_POLL_FAST_MINUTES`, `LOCAL_POLL_SLOW_MINUTES`, `LOCAL_MADEONSOL_MINUTES`, `LOCAL_JOB_TIMEOUT_MINUTES` in `.env`.
