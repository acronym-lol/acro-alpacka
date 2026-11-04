# Reference Document

This file gets baked into the system prompt and cached on the first
request. Replace it with your own content — a product spec, a knowledge
base, a style guide, an API reference — and Claude will answer questions
grounded in it.

The bigger this file is, the more cache savings you'll see. Caching pays
off above ~2K tokens (Sonnet) or ~4K tokens (Opus / Haiku). Below that,
the request is too small to cache and you'll see `cache_read: 0` in the
per-turn stats.

## Try asking

- "What is this document about?"
- "Summarize the key points."
- "What's the cache hit threshold for Sonnet?"

The first turn writes the cache (look for `cache_write:` > 0). Every
turn after that, within 5 minutes, reads from it (`cache_read:` > 0,
~10× cheaper).

## Replacing this file

Drop a markdown / text / code file at the path you set in
`REFERENCE_FILE` (default: `reference.md`). The script reads it once at
startup; restart Claude (the launcher's Stop + Launch buttons) to pick
up changes.
