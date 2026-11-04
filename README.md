# rippling

Unofficial command-line client for [Rippling](https://www.rippling.com): log working hours,
breaks and vacation and check your vacation balance from the terminal.

## Install

```bash
uv tool install rippling
```

`rippling login` opens Rippling in a Chromium-based browser you already have (Chrome, Edge,
Brave, Chromium or Helium). Set `RIPPLING_BROWSER` to a browser executable to pick one.

## Use

```bash
rippling login                                        # browser window: log in, 2FA; done
rippling balance
rippling hours today 09:00 18:00 --break 12:00-13:00
rippling vacation 2026-12-21 2026-12-23 --reason "Winter break"
```

**Status: early.** `balance` and `hours` are verified against the live API. The `vacation`
payload mirrors what the web app's code sends but hasn't been confirmed by a live submission
yet; check the result in the web app after your first request.

**Writes are dry-run by default.** `hours` and `vacation` print exactly what they would send
and ask before sending. Without a terminal (scripts, agents) they stop after the dry-run;
pass `--yes` to send.

- `hours` sets one day: it creates the entry or replaces that day's existing one completely
  (times and breaks). It can't delete entries, and it refuses to replace entries with job codes.
- `vacation` uses the Annual Leave Policy, end date inclusive, and runs Rippling's own
  validation first. Sending notifies your approver.

## How auth works

There is no public API key for employees. `rippling login` opens your browser with a separate
profile just for this tool, you log in normally, and the CLI reads the session token from the
web app over the browser's local DevTools port and stores it in
`~/.config/rippling/credentials.json` (mode 0600; override the folder with
`RIPPLING_CONFIG_DIR`). The token lasts about 30 days. The browser profile keeps your session,
so later logins are usually instant.

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
may break at any time, and using it may violate Rippling's terms or your employer's policies.
Use at your own risk.
