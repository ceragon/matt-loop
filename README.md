# matt-loop

**Loop engineering: one ticket, one loop.** A minimal dispatch loop that drives
terminal-based coding agents ticket by ticket — the end goal is an empty issue
tracker.

## Agent bootstrap checklist (agents: read this first)

An executable decision list for any caller agent picking up this repo. Probe in
order; **when anything is missing, present the full steps to the user and wait
for explicit approval before installing** — scriptable parts may then run
automatically, GUI steps stay with the human. `hc` and `herdr-app` are hard
dependencies: without them the skill refuses to run (zero sends); there is no
degraded round without Herdr.

1. **Probe `hc`** (the herdr CLI): `command -v hc` — also check `~/.local/bin`
   if PATH is minimal. Missing → go to "Installing Herdr" below, present the
   steps, wait for the user's nod.
2. **Probe the desktop app**: `herdr-app status` (exit code tells you whether
   the app is running). **Never invoke bare `herdr-app` as a probe — it opens a
   window.** Not installed → install steps below. Installed but not running →
   this is a human step: ask the user to open herdr-app, log in, and **keep the
   window open** (named sessions must stay visible in the workspace; this is a
   hard invariant, not a preference).
3. **Determine the extraction tier** (decided once at admission, recorded as
   `extract_backend` in every result):
   - `LOOP_EXTRACT_BACKEND` explicitly set (`api` | `pi` | `caller`) → that tier
     wins, always.
   - Unset, but **any `LOOP_API_*` variable set** → api intent: all three of
     `LOOP_API_BASE` / `LOOP_API_KEY` / `LOOP_API_MODEL` must be configured, or
     the round fails loudly with `api_not_configured` (half-configured typos do
     not silently fall through).
   - All three unset → `pi` found on PATH (or common tool dirs) → pi tier.
   - Nothing configured and no pi → **caller tier, the zero-config floor**: the
     round still runs end to end; extraction is skipped and the notification
     inlines the turn body (`turn_text`) for *you* to read and interpret.
4. **Act per tier**: api → verify the three env vars; pi → verify `pi` runs;
   caller → nothing to configure, but read the caller branch of the delivery
   contract in `loop/SKILL.md` before interpreting results (empty selection
   fields mean "not yet identified", not "no content").

The full flag contract, result schema, and error kinds live in `loop/SKILL.md`
and `loop/REFERENCE.md` — not repeated here.

## Installing Herdr (present to the user; run only after approval)

Official installer (publicly available; verified at the time of writing with
app 0.1.18 / core 0.1.22):

- **macOS / Linux**:

  ```sh
  curl -fsSL https://releases.herdr.dev/app/install.sh | sh
  ```

  The script verifies sha256 checksums; the app lands in `~/Applications`,
  the `hc` CLI in `~/.local/bin` (make sure that directory is on PATH).
- **Windows**: use `install.ps1` from the same releases page.

Human-only steps after installation (list them for the user, do not pretend to
automate them): open herdr-app, log in, and keep the window open while rounds
run. Then re-run the probe checklist above.

## What this is

One skill directory plus one scripts directory (`loop/scripts/`), the deployment
unit — `dispatch.py` and its sibling modules are synced together as a whole. A
caller agent (any LLM agent that can run shell commands) uses it to:

1. send an explicitly released prompt into a running executor-agent terminal
   session (managed by herdr's `hc` CLI),
2. wait for the turn to settle,
3. trim and extract the reply into structured JSON — three tiers
   (api → pi → caller): direct OpenAI-compatible API, `pi` as an explicit
   fallback, or caller self-extraction when neither is configured,
4. optionally POST a notification projection (`notify.json`, byte-identical to the
   request body) to a webhook so the caller can be woken up. The full `result.json`
   stays the audit/review source of truth; callers read the narrow projection first.

The script does **mechanics only**. Every workflow decision lives in the
caller's prompts; the main program only controls the loop. No wrappers, no
tripwires, no audit logs, no outer polling.

## One round

```
release id + prompt file + target session (name recommended)
  → prechecks (get-or-create triage, identity, visibility, idle state, session discipline)
  → optional presteps: --launch (create/revive) / --reset / --target-model
  → send prompt (never forced)
  → wait for settle → capture screen → extract (or caller-tier inline) → result JSON
  → ledger entry + optional webhook notification
```

Safety contract highlights:

- A prompt is sent only with a unique, explicit **release id**; one release
  runs exactly one round.
- Session-start skills (wayfinder, grilling, implement, …) require a fresh
  (`turn == 0`, or freshly created/revived) or explicitly reset session;
  otherwise the round is refused.
- Named sessions are always visible in the herdr-app workspace (adoption is a
  hard invariant; the app must stay open). Occupied names are reported, never
  silently taken over; `--rebuild` kills only idle occupants, only with the
  user's explicit authorization.
- `blocked` states stop the round and surface to the caller — never swallowed.
- Secrets never appear in stdout, result files, or stderr.

## Repository layout

```
loop/            the skill: SKILL.md, REFERENCE.md, prompts/, scripts/
docs/adr/        architecture decision records (in Chinese)
docs/agents/     issue-tracker and triage conventions for agents
GLOSSARY.md      domain vocabulary (in Chinese)
```

## Quick start (humans)

Requirements: Python 3.10+, `herdr` (`hc`) managing the executor terminal,
`herdr-app` (the desktop window, kept open — visibility is a hard invariant),
and an executor agent (e.g. `claude`, `pi`, `codex`). An OpenAI-compatible
extraction endpoint is **recommended, not required**: with nothing configured
the loop falls back to the caller tier and still completes a round.

1. Copy `loop/webhook.env.example`, fill in your own values, and keep it
   **outside the repo** (`chmod 600`). Never commit real credentials.
2. Run one round:

   ```sh
   python3 loop/scripts/dispatch.py \
     --target <session-name-or-terminal> \
     --release-id <unique-id> \
     --prompt-file <path-to-released-prompt.md> \
     --space <workspace-space> \
     --expect-proc <executor-agent-name>
   ```

The full flag contract, result schema, and error kinds are documented in
`loop/SKILL.md` and `loop/REFERENCE.md` (in Chinese).

## Privacy boundaries (public repo)

- Never commit real secrets or tokens, webhook URLs with token paths,
  machine-specific absolute paths, usernames, hostnames, or internal IP
  addresses. Use placeholders (`<USER>`, `<HOST>`, `<KEY>`,
  `https://example.invalid`).
- `.loop/` runtime ledgers and `webhook.env` are gitignored; only
  `webhook.env.example` is tracked.
- CI runs a whole-repo hygiene scan on every pull request. Hits report
  file, line, and category only — never the matched content (CI logs are
  public too).

## Issues and pull requests

Issues live in GitHub Issues; agent-facing conventions are in
`docs/agents/issue-tracker.md`. Issue templates carry the same privacy
reminders. `main` is protected: all changes land through pull requests.

## License

[MIT](LICENSE)