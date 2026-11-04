---
name: rippling
description: Log and review working hours, request or cancel vacation, check leave balances and holidays, and look up colleagues in Rippling. Use whenever the user wants to record or fix time worked, see what they logged this week or month, request or cancel time off, asks how many vacation days they have (now or on a future date), whether a day is a holiday, or who someone is (title, team, manager, work email). Wraps the unofficial `rippling` CLI, which talks to Rippling's web API with the user's own login.
metadata:
  version: "0.2.0"
---

# rippling - hours, breaks, vacation

`rippling` reads and writes the user's own Rippling time tracking and leave data. The CLI is
self-documenting: `rippling --help` and `rippling <command> --help`. Install with
`uv tool install rippling` (or run ad hoc with `uvx rippling ...`).

## Writes need the user's yes. No exceptions.

`hours log`, `leave request` and `leave cancel` are **dry-run by default**: they print a summary plus the exact JSON
they would send. Run from an agent (no terminal on stdin) they then stop ("Dry run: nothing
sent"); in a terminal they ask the user instead. To send:

1. Run the command without `--yes`.
2. Show the user the summary lines (date, times, breaks, working days, warnings).
3. Only after the user explicitly approves *this* entry, re-run the identical command with `--yes`.

Never send test, example or throwaway entries. Leave requests (and possibly cancellations)
notify the user's approver.
Don't retry a failed write blindly: read the error, and check the current state first (a
dry-run of the same command shows what exists).

## Commands

Reads (safe, take `--json` - prefer it when you parse the output):

```bash
rippling hours week [DATE] [--json]            # Mon-Sun: entries, breaks, worked vs scheduled, missing days
rippling hours month [YYYY-MM] [--json]
rippling leave balance [--on YYYY-MM-DD] [--json]   # days; --on adds Rippling's projection
rippling leave list [--all] [--json]           # requests with status and id
rippling holidays [--year YYYY] [--json]
rippling people search [QUERY] [--json]        # name/title/department/email words; manager, email
rippling whoami [--json]
```

Writes (dry-run unless `--yes`, see above):

```bash
rippling hours log DATE START END [--break HH:MM-HH:MM ...] [--yes]
rippling leave request FIRST-DAY LAST-DAY [--reason TEXT] [--yes]
rippling leave cancel REQUEST-ID [--yes]       # id or its first 6+ chars from `leave list`
```

- Dates are `YYYY-MM-DD`; `hours` also takes `today` / `yesterday`. Times are 24h local
  wall-clock (the CLI converts to UTC with DST using the user's Rippling timezone).
- Before logging hours, `hours week --json` shows what already exists and which days are
  `missing`. `hours log` sets one day: it creates the entry, or **replaces** that day's
  existing entry (the dry-run shows `now:` vs `new:`). Breaks must lie inside the working time.
- `leave request` uses the Annual Leave Policy, end date inclusive. The dry-run shows the
  estimated working days, holidays in range, overlaps, and Rippling's own validation result.
- `people search` returns colleagues' work data. Use it to answer the user's question; don't
  store or forward it beyond that.

## Login

Any command failing with "Run `rippling login`" means the token is missing or expired
(it lasts ~30 days). `rippling login` opens a visible browser window where the **user** must
type their password and 2FA. Ask the user to run it (or run it and tell them to complete
the login in the window). Never handle their password yourself.
