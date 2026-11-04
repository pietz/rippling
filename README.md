# rippling

Unofficial command-line client for [Rippling](https://www.rippling.com): log working hours,
breaks and vacation and check your vacation balance from the terminal.

> [!WARNING]
> **Unofficial. Use at your own risk.** This project is not affiliated with, endorsed by or
> supported by People Center, Inc. (Rippling). "Rippling" is a trademark of People Center, Inc.,
> used here only to describe what the tool works with.
>
> It talks to the undocumented web API that the Rippling web app uses, with your own login.
> **Using it may violate Rippling's terms of service and your employer's policies**, and may get
> your access suspended. That API can change or break at any time. Check with your employer
> before using it. You alone are responsible for your use of this tool and for every time or
> leave entry it sends. Provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).

## Install

```bash
uv tool install rippling
```

`rippling login` also needs [agent-browser](https://github.com/vercel-labs/agent-browser)
(`npm i -g agent-browser`). It uses [Helium](https://helium.computer) when installed and
otherwise agent-browser's own Chrome; set `RIPPLING_BROWSER` to a browser executable to choose.

## Use

```bash
rippling login                                        # browser window: log in, 2FA; done
rippling balance
rippling hours today 09:00 18:00 --break 12:00-13:00
rippling vacation 2026-12-21 2026-12-23 --reason "Winter break"
```

**Status: early.** Reading balances is verified against the live API. The write payloads
(`hours`, `vacation`) mirror what the web app's code sends but haven't been confirmed by a live
submission yet; check the result in the web app after your first writes.

**Writes are dry-run by default.** `hours` and `vacation` print exactly what they would send
and ask before sending. Without a terminal (scripts, agents) they stop after the dry-run;
pass `--yes` to send.

- `hours` sets one day: it creates the entry or replaces that day's existing one completely
  (times and breaks). It can't delete entries, and it refuses to replace entries with job codes.
- `vacation` uses the Annual Leave Policy, end date inclusive, and runs Rippling's own
  validation first. Sending notifies your approver.

## How auth works

There is no public API key for employees. `rippling login` opens a browser with a dedicated
profile, you log in normally, and the CLI copies the session token from the web app into
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
