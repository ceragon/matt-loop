# matt-loop

**Loop engineering: one ticket, one loop.** A minimal dispatch loop that drives
terminal-based coding agents ticket by ticket — the end goal is an empty issue
tracker.

## What this is

One skill directory plus one Python script (`loop/scripts/dispatch.py`). A
caller agent (any LLM agent that can run shell commands) uses it to:

1. send an explicitly released prompt into a running executor-agent terminal
   session (managed by herdr's `hc` CLI),
2. wait for the turn to settle,
3. trim and extract the reply into structured JSON (direct OpenAI-compatible
   API by default, `pi` as an explicit fallback),
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
  → wait for settle → capture screen → extract → result JSON
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

## Quick start

Requirements: Python 3.12+, `herdr` (`hc`) managing the executor terminal,
`herdr-app` (the desktop window, kept open — visibility is a hard invariant),
an executor agent (e.g. `claude`, `pi`, `codex`), and an OpenAI-compatible
endpoint for extraction.

1. Copy `loop/webhook.env.example`, fill in your own values, and keep it
   **outside the repo** (`chmod 600`). Never commit real credentials.
2. Run one round:

   ```sh
   python3 loop/scripts/dispatch.py \
     --target <session-name-or-terminal> \
     --release-id <unique-id> \
     --prompt-file <path-to-released-prompt.md> \
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
