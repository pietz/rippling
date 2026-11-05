# rippling

Unofficial command-line client for [Rippling](https://www.rippling.com): log and review working
hours, request and cancel vacation, check balances and holidays, see what's waiting in your inbox,
and look up colleagues from the terminal.

## Install

```bash
uv tool install rippling
```

`rippling login` opens Rippling in a Chromium-based browser you already have (Chrome, Edge,
Brave, Vivaldi, Chromium or Helium). Set `RIPPLING_BROWSER` to a browser executable to pick one.

## Use

```bash
rippling login                                   # browser window: log in once, ~30 days

rippling hours log today 09:00 18:00 --break 12:00-13:00
rippling hours delete yesterday                  # undo: remove that day's entry
rippling hours week                              # this week: entries, breaks, missing days
rippling hours month 2026-10
rippling hours period                            # pay period totals, overtime, alerts
rippling hours changes 2026-10-28                # who edited which entry in that pay period

rippling leave balance --on 2027-04-01           # today's balance plus a projection
rippling leave history --policy "Annual Leave Policy"   # accruals, requests, expiry
rippling leave request 2026-12-21 2026-12-23 --reason "Winter break"
rippling leave list
rippling leave cancel 1a2b3c
rippling leave ics -o leave.ics                  # leave and holidays for your calendar

rippling holidays 2027
rippling todo                                    # inbox items that need you (marks nothing read)
rippling people search engineering
rippling whoami
rippling logout                                  # delete the local token and browser profile
```

Every command explains itself with `--help`. Read commands take `--json` for scripts and agents.

**Writes are dry-run by default.** `hours log`, `hours delete`, `leave request` and
`leave cancel` print exactly what they would send and ask before sending. Without a terminal
(scripts, agents) they stop after the dry-run; pass `--yes` to send. A write is sent once and
never retried; if the outcome is unclear (timeout, server error), the CLI says so: check before
trying again.

- `hours log` sets one day: it creates the entry or replaces that day's existing one completely
  (times and breaks), and checks the day again right before sending. It refuses to touch what
  it can't carry over (several entries on the day, job codes, attachments, comments, custom
  inputs, overnight shifts, locked entries) and days outside Rippling's current pay periods.
- `hours delete` removes a day's single entry, with the same checks as `hours log`. Like
  `hours log`, it works on approved entries; the preview says when an entry is approved.
- `leave request` uses the Annual Leave Policy (`--policy` picks another), end date inclusive,
  and runs Rippling's own validation first. Sending notifies your approver.

**Status: early.** Reads and `hours log` are verified against the live API. `hours delete`,
`leave request` and `leave cancel` mirror what the web app's code sends but haven't been
confirmed by a live submission yet; check the result in the web app after first use.

## How auth works

There is no public API key for employees. `rippling login` opens your browser with a separate
profile just for this tool, you log in normally, and the CLI reads the session token from the
web app over the browser's local DevTools port and stores it in
`~/.config/rippling/credentials.json` (mode 0600; override the folder with
`RIPPLING_CONFIG_DIR`). The token lasts about 30 days. The browser profile (in the same folder) keeps your session,
so later logins are usually instant. Both are stored unencrypted, protected only by your user
account; `rippling logout` deletes both. That doesn't revoke the token on Rippling's side; it
stays valid until it expires.

## Agent skill

`skills/rippling/SKILL.md` teaches coding agents to use the CLI, including the rule that
every write needs your explicit yes.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

Unofficial and not affiliated with Rippling. It uses Rippling's undocumented web API, which
may break at any time. Using it likely conflicts with Rippling's terms of use and may conflict
with your employer's agreement with Rippling and its IT policies: **get your employer's written
approval before using it**, and only use it for your own account. Time and leave records
matter for payroll; you are responsible for every entry you send. Use at your own risk.
