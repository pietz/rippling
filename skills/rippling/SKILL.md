---
name: rippling
description: Rippling HR via the `rippling` CLI. Use when the user wants to log, delete or check working hours (week, month, pay period, edit history), request or cancel vacation, see leave balances, balance history or holidays, export leave to a calendar, check what needs their attention in Rippling, or look up a colleague (title, team, manager, email).
---

# rippling

Run `rippling --help` and `rippling <group> --help`; the CLI documents itself. Not installed? Use `uvx rippling ...` instead.

## Writes need the user's yes

`hours log`, `hours delete`, `leave request` and `leave cancel` print a preview and send nothing without `--yes` (in an interactive terminal they ask instead; never answer that prompt yourself). Run without `--yes`, show the user the summary, and only after they approve that exact action, re-run the same command with `--yes`. Never send test entries. Leave requests notify the approver.

## Common patterns

```bash
rippling todo --json                                         # anything waiting for the user
rippling hours week --json                                   # what's logged, which days are missing
rippling hours log today 09:00 18:00 --break 12:00-13:00     # then --yes after approval
rippling leave balance --on 2027-01-01                       # days left now and projected
rippling leave request 2026-12-21 2026-12-23 --reason "..."  # dry-run shows working days + validation
rippling people search "jane engineering" --json
```

## Gotchas

- `hours log` replaces that day's existing entry completely; check `hours week` first. It refuses days it can't replace safely (several entries, attachments, comments, overnight shifts): the user edits those in the web app. `hours delete` removes the day's entry with the same checks (and refuses approved entries).
- Dates `YYYY-MM-DD`, `today` or `yesterday`; local 24h times; prefer `--json` when parsing.
- `leave request` uses the "Annual Leave Policy"; other policies via `--policy "<name from leave balance>"`.
- "Run `rippling login`" means the ~30-day token expired. Login opens a browser where the user types password and 2FA themselves: ask them to run it.
