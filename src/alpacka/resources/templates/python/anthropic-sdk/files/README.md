# {{PROJECT_NAME}}

Claude API starter — multi-turn Q&A over a reference document, with
prompt caching, adaptive thinking, refusal fallbacks, and proper
`stop_reason` handling.

## Setup

```
python -m venv .venv
.venv/Scripts/activate         # Windows
source .venv/bin/activate      # macOS / Linux
pip install -e .
cp .env.example .env           # then edit and add your API key
```

Get a key at <https://console.anthropic.com/>. Set `ANTHROPIC_API_KEY`
in `.env` (or in `alpacka.yml` if you don't mind committing it — but
prefer `alpacka.local.yml` for secrets).

## Run

Click Launch in ALPACKA, or:

```
python main.py
```

You'll see a `You: ` prompt. Ask questions, hit Enter, get a reply.
Empty line or `quit` to exit.

After each reply you'll see token stats:

```
[tokens — input: 12, output: 187, cache_read: 1843, cache_write: 0]
```

- **First cached turn**: `cache_write` is non-zero — the prompt got
  written to cache (~1.25× normal cost).
- **Every later turn within 5 min**: `cache_read` is non-zero — the
  cached portion is served at a small fraction of the normal input
  price (5–10%, depending on the model).

A prompt only caches once the cached part is at least 512 tokens long
on the current models. The sample `reference.md` is shorter than that,
so the first turns show `cache_write: 0` until the conversation grows.
Put your own, longer document in `reference.md` to see caching from the
first turn.

## Variables

All set in `alpacka.yml`'s `variables:` section (or via Edit Details
in the launcher):

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Which Claude model: `claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5` |
| `EFFORT` | `medium` | How much Claude thinks: `low` / `medium` / `high` / `xhigh` / `max` |
| `REFERENCE_FILE` | `reference.md` | Path to the doc baked into the system prompt |

## What's worth keeping when you adapt this

- **The `cache_control` placement.** System prompt gets a manual
  breakpoint; the rolling conversation tail gets the top-level
  auto-placed one. Two breakpoints cover the common cases — max is 4.
- **Stateless conversation.** The `conversation` list IS the state.
  Anthropic's API is stateless; you send the full history every turn.
- **Send replies back unchanged.** Each assistant turn goes back exactly
  as it came, thinking blocks included. Don't edit, reorder or trim
  earlier turns, or rebuild the system prompt mid-conversation: the API
  checks that earlier turns are unchanged, and an edit invalidates the
  thinking blocks after it (and the prompt cache). `reply_to_keep()`
  handles the one exception, a reply a fallback model took over.
- **Thinking stays on.** The current models don't let you turn thinking
  off; lower `EFFORT` for faster, cheaper replies instead.
- **Refusal fallbacks.** `fallbacks: "default"` (beta
  `server-side-fallback-2026-07-01`, on `client.beta.messages`) re-runs
  a request the model's safety classifiers decline on a fallback model,
  in the same call. Haiku has no fallback model, so a declined request
  stays declined.
- **`stop_reason` handling.** Refusal → don't append to history (the
  user message wasn't really answered). max_tokens → surface so the
  user knows their reply might be truncated.
- **Try / except around the API call.** Rate limits and 5xx errors
  are real. The SDK auto-retries 429 and 5xx with backoff, but they
  can still surface; handle them so the chat doesn't crash.

## Where to go next

- **Tools.** Add a tool definition in `tools=[...]`, handle
  `stop_reason == "tool_use"` (or use `client.beta.messages.tool_runner`
  to skip the manual loop). See
  <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>.
- **Streaming.** For replies > ~16K tokens, switch to
  `client.beta.messages.stream(**request)` — see the commented block at
  the bottom of `main.py`.
- **Other models.** All three options in `ANTHROPIC_MODEL` work the same
  way; swap and re-run. Opus 5.5 is best for the hardest tasks; Haiku
  5.5 is fastest and cheapest.
- **Switch to a structured output.** If you want JSON instead of free
  text, set `output_config.format` to a JSON schema; Claude will
  conform.
