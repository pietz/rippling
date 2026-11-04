---
name: rippling
description: Log working hours, breaks and vacation in Rippling and check the vacation balance. Use whenever the user wants to record or fix time worked on a day, request time off, or asks how many vacation days they have left. Wraps the unofficial `rippling` CLI, which talks to Rippling's web API with the user's own login.
metadata:
  version: "0.1.0"
---

# rippling - hours, breaks, vacation

`rippling` reads and writes the user's own Rippling time tracking and leave data. The CLI is
self-documenting: `rippling --help` and `rippling <command> --help`. Install with
`uv tool install rippling` (or run ad hoc with `uvx rippling ...`).

## Writes need the user's yes. No exceptions.

`hours` and `vacation` are **dry-run by default**: they print a summary plus the exact JSON
they would send, then stop ("Dry run: nothing sent"). To send:

1. Run the command without `--yes`.
2. Show the user the summary lines (date, times, breaks, working days, warnings).
3. Only after the user explicitly approves *this* entry, re-run the identical command with `--yes`.

Never send test, example or throwaway entries. A vacation request notifies the user's approver.
Don't retry a failed write blindly: read the error, and check the current state first (a
dry-run of the same command shows what exists).

## Commands

```bash
rippling balance [--json]                       # leave balances in days
rippling hours <date> <start> <end> [--break HH:MM-HH:MM ...] [--yes]
rippling vacation <first-day> <last-day> [--reason TEXT] [--yes]
rippling login                                  # needs the human, see below
```

- Dates are `YYYY-MM-DD`; `hours` also takes `today` / `yesterday`. Times are 24h local
  wall-clock (the CLI converts to UTC with DST using the user's Rippling timezone).
- `hours` sets one day: it creates the entry, or **replaces** that day's existing entry
  (the dry-run shows `now:` vs `new:`). Breaks must lie inside the working time.
- `vacation` uses the Annual Leave Policy, end date inclusive. The dry-run shows the
  estimated working days, holidays in range, overlaps, and Rippling's own validation result.

## Login

Any command failing with "Run `rippling login`" means the token is missing or expired
(it lasts ~30 days). `rippling login` opens a visible browser window where the **user** must
type their password and 2FA. Ask the user to run it (or run it and tell them to complete
the login in the window). Never handle their password yourself.
