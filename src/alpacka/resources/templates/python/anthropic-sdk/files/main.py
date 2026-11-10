"""Claude API starter — multi-turn Q&A over a reference document.

Demonstrates the patterns you'll likely want for a real Claude app:

  - **Prompt caching** on the system prompt (reference doc) and the
    rolling conversation tail. Prints cache hit stats per turn so you
    can watch caching pay off.
  - **Adaptive thinking** with the `effort` parameter — Claude decides
    how much to think per request; lower `EFFORT` for faster replies.
  - **Multi-turn conversation** kept in a Python list (Anthropic's API
    is stateless — you send the full history every turn). Each reply is
    sent back exactly as it came, thinking blocks included.
  - **Refusal fallbacks** — if the model's safety classifiers decline a
    request, the API re-runs it on a fallback model in the same call.
  - **`stop_reason` handling** — refusal, max_tokens, end_turn.

Run via `python main.py` (or click Launch in ALPACKA). Env vars
(loaded from `.env` if present, or from the alpacka.yml's `variables:`
section at launch time):

  ANTHROPIC_API_KEY      — your key (required)
  ANTHROPIC_MODEL        — claude-opus-5-5 (default), claude-sonnet-5-5,
                           claude-haiku-5-5
  EFFORT                 — low | medium | high | xhigh | max
  REFERENCE_FILE         — path to a markdown/text file (default reference.md)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("EFFORT", "medium")
REFERENCE_FILE = os.environ.get("REFERENCE_FILE", "reference.md")

# Thinking counts toward max_tokens, so leave room for it. 16K keeps a
# non-streaming request well inside the SDK's HTTP timeout; stream (see the
# bottom of this file) if you need more.
MAX_OUTPUT_TOKENS = 16000

# `fallbacks: "default"` lets the API pick the fallback model by refusal
# category. It needs this beta header and the beta messages endpoint.
# Haiku has no fallback model: a declined request simply stays declined.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# ---------------------------------------------------------------------------
# System prompt — large + frozen, perfect for caching
# ---------------------------------------------------------------------------

def load_reference() -> str:
    """Read the reference doc that gets baked into the system prompt."""
    path = Path(REFERENCE_FILE)
    if not path.is_file():
        sys.exit(
            f"reference file not found: {path}. Set REFERENCE_FILE in alpacka.yml "
            f"or drop a file at that path."
        )
    return path.read_text(encoding="utf-8")


def build_system(reference: str) -> list[dict]:
    """System prompt as a single text block with explicit cache_control.

    Caching the system prompt means the doc only counts as full-price
    input tokens on the FIRST request. Every subsequent turn within the
    cache TTL (5 min) pays a small fraction of that (5–10%, depending on
    the model) for that block. Keep it frozen for the
    whole conversation: changing it mid-conversation resets the cache and
    invalidates earlier thinking blocks.
    """
    text = (
        "You are a helpful assistant that answers questions about the "
        "following reference document. Cite specific sections when "
        "possible. If the answer isn't in the doc, say so explicitly.\n\n"
        "<reference>\n" + reference + "\n</reference>"
    )
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def reply_to_keep(content: list) -> list:
    """The assistant turn to send back next time.

    Normally that's the reply exactly as it came, thinking blocks
    included: the API checks that earlier turns are unchanged, and editing
    them (stripping a thinking block from the middle, say) invalidates the
    thinking blocks after it. The one exception is a reply where a
    fallback model took over: before the last `fallback` marker, only text
    goes back, and the marker itself is dropped.
    """
    last_fallback = max(
        (i for i, block in enumerate(content) if block.type == "fallback"),
        default=-1,
    )
    return [
        block
        for i, block in enumerate(content)
        if i > last_fallback or block.type == "text"
    ]


# ---------------------------------------------------------------------------
# Conversation loop
# ---------------------------------------------------------------------------


def run() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "ANTHROPIC_API_KEY not set. Add it to .env or set it as an "
            "env var in alpacka.yml's variables: section."
        )

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
    reference = load_reference()
    system = build_system(reference)
    conversation: list[dict] = []

    print(f"Model: {MODEL}")
    print(f"Reference: {REFERENCE_FILE} ({len(reference):,} chars)")
    print(f"Effort: {EFFORT}")
    print(f"Type your question and press Enter. Empty line or 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break

        conversation.append({"role": "user", "content": user_input})

        # Build the request. Top-level cache_control auto-places a breakpoint
        # on the LAST cacheable block — i.e., the user message we just
        # added. Combined with the breakpoint on the system prompt, every
        # turn after the first reads BOTH cached pieces.
        request = {
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": conversation,
            "cache_control": {"type": "ephemeral"},
            # Thinking can't be switched off on the current models; lower
            # the effort instead when you want faster, cheaper replies.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": EFFORT},
            "fallbacks": "default",
            "betas": [FALLBACK_BETA],
        }

        try:
            response = client.beta.messages.create(**request)
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "60")
            print(f"[rate-limited; retry after {retry_after}s]\n")
            conversation.pop()  # drop the unsent user turn
            continue
        except anthropic.APIStatusError as exc:
            print(f"[api error {exc.status_code}: {exc.message}]\n")
            conversation.pop()
            continue

        # stop_reason values: end_turn, max_tokens, stop_sequence, tool_use,
        # pause_turn, refusal. We don't use tools here; refusal + max_tokens
        # are the ones to surface. With fallbacks on, a refusal means the
        # fallback model declined too.
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            print(f"Claude: [declined to answer (category: {category})]\n")
            conversation.pop()
            continue
        if response.stop_reason == "max_tokens":
            print(
                f"[hit max_tokens={MAX_OUTPUT_TOKENS}; raise it or stream "
                f"if you need a longer reply]"
            )

        conversation.append({"role": "assistant", "content": reply_to_keep(response.content)})

        text = "".join(b.text for b in response.content if b.type == "text")
        print(f"\nClaude: {text}\n")
        if response.model != MODEL:
            print(f"  [answered by the fallback model {response.model}]")

        u = response.usage
        print(
            f"  [tokens — input: {u.input_tokens}, output: {u.output_tokens}, "
            f"cache_read: {u.cache_read_input_tokens}, "
            f"cache_write: {u.cache_creation_input_tokens}]\n"
        )


if __name__ == "__main__":
    run()


# ---------------------------------------------------------------------------
# Streaming variant — use it if your replies routinely exceed ~16K tokens
# (non-streaming hits SDK HTTP timeouts above that). Otherwise non-streaming
# is simpler.
#
#     with client.beta.messages.stream(**request) as stream:
#         for text in stream.text_stream:
#             print(text, end="", flush=True)
#         response = stream.get_final_message()
# ---------------------------------------------------------------------------
