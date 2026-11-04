# {{PROJECT_NAME}}

Claude API starter — multi-turn Q&A over a reference document, with
prompt caching, adaptive thinking, and proper `stop_reason` handling.

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

- **First turn**: `cache_write` is non-zero — the system prompt got
  written to cache (~1.25× normal cost).
- **Every later turn within 5 min**: `cache_read` is non-zero — the
  cached portion is served at ~0.1× cost.

If `cache_read` stays at 0 across turns, your reference doc is
probably below the model's minimum (2048 tokens for Sonnet 4.6,
4096 for Opus / Haiku). Add more content to `reference.md` to see
caching kick in.

## Variables

All set in `alpacka.yml`'s `variables:` section (or via Edit Details
in the launcher):

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Which Claude model |
| `USE_ADAPTIVE_THINKING` | `true` | Adaptive thinking on/off |
| `EFFORT` | `medium` | `low` / `medium` / `high` / `max` (max = Opus only) |
| `REFERENCE_FILE` | `reference.md` | Path to the doc baked into the system prompt |

## What's worth keeping when you adapt this

- **The `cache_control` placement.** System prompt gets a manual
  breakpoint; the rolling conversation tail gets the top-level
  auto-placed one. Two breakpoints cover the common cases — max is 4.
- **Stateless conversation.** The `conversation` list IS the state.
  Anthropic's API is stateless; you send the full history every turn.
- **`stop_reason` handling.** Refusal → don't append to history (the
  user message wasn't really answered). max_tokens → surface so the
  user knows their reply might be truncated.
- **Try / except around the API call.** Rate limits and 5xx errors
  are real. The SDK auto-retries 429 and 5xx with backoff, but they
  can still surface; handle them so the chat doesn't crash.

## Where to go next

- **Tools.** Add a tool definition in `tools=[...]`, handle
  `stop_reason == "tool_use"` (or use `client.beta.messages.tool_runner`
  to skip the manual loop). See <https://docs.claude.com/api/tool-use>.
- **Streaming.** For replies > ~16K tokens, switch to
  `client.messages.stream(**request)` — see the commented block at the
  bottom of `main.py`.
- **Other models.** All three options in `ANTHROPIC_MODEL` work
  identically; swap and re-run. Opus 4.7 is best for hardest tasks;
  Haiku 4.5 is fastest / cheapest.
- **Switch to a structured output.** If you want JSON instead of free
  text, set `output_config.format` to a JSON schema; Claude will
  conform.
