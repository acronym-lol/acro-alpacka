# Reference Document

This file gets baked into the system prompt and cached. Replace it with
your own content — a product spec, a knowledge base, a style guide, an
API reference — and Claude will answer questions grounded in it.

The bigger this file is, the more cache savings you'll see. A prompt
only caches once the cached part is at least 512 tokens long (about
2,000 characters of English) on the current models. This sample is
shorter than that, so the first turns show `cache_write: 0`; caching
starts as the conversation grows, or straight away with a longer file.

## Try asking

- "What is this document about?"
- "Summarize the key points."
- "How long does a prompt need to be before it's cached?"

Once caching starts, the first cached turn writes the cache (look for
`cache_write:` > 0). Every turn after that, within 5 minutes, reads from
it (`cache_read:` > 0, at a small fraction of the normal input price).

## Replacing this file

Drop a markdown / text / code file at the path you set in
`REFERENCE_FILE` (default: `reference.md`). The script reads it once at
startup; restart Claude (the launcher's Stop + Launch buttons) to pick
up changes.
