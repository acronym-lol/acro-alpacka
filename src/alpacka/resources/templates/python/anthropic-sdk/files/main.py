"""Claude API starter — multi-turn Q&A over a reference document.

Demonstrates the patterns you'll likely want for a real Claude app:

  - **Prompt caching** on the system prompt (reference doc) and the
    rolling conversation tail. Prints cache hit stats per turn so you
    can watch caching pay off.
  - **Adaptive thinking** with the `effort` parameter — Claude decides
    depth per request. Toggle off for latency-sensitive flows.
  - **Multi-turn conversation** kept in a Python list (Anthropic's API
    is stateless — you send the full history every turn).
  - **`stop_reason` handling** — refusal, max_tokens, end_turn.

Run via `python main.py` (or click Launch in ALPACKA). Env vars
(loaded from `.env` if present, or from the alpacka.yml's `variables:`
section at launch time):

  ANTHROPIC_API_KEY      — your key (required)
  ANTHROPIC_MODEL        — claude-sonnet-4-6 (default), opus-4-7, haiku-4-5
  USE_ADAPTIVE_THINKING  — true / false
  EFFORT                 — low | medium | high | max  (max = Opus only)
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

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
USE_THINKING = os.environ.get("USE_ADAPTIVE_THINKING", "true").lower() in (
    "1", "true", "yes", "on",
)
EFFORT = os.environ.get("EFFORT", "medium")
REFERENCE_FILE = os.environ.get("REFERENCE_FILE", "reference.md")

# Sonnet/Haiku cap at 64K output, Opus at 128K. 8K is plenty for chat replies
# and keeps responses snappy.
MAX_OUTPUT_TOKENS = 8000


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
    cache TTL (5 min) costs ~0.1x for that block.
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
    print(f"Adaptive thinking: {USE_THINKING}, effort: {EFFORT}")
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
        }
        if USE_THINKING:
            request["thinking"] = {"type": "adaptive"}
            request["output_config"] = {"effort": EFFORT}
        else:
            request["thinking"] = {"type": "disabled"}

        try:
            response = client.messages.create(**request)
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
        # are the ones to surface.
        if response.stop_reason == "refusal":
            print("Claude: [declined to answer for safety reasons]\n")
            conversation.pop()
            continue
        if response.stop_reason == "max_tokens":
            print(
                f"[hit max_tokens={MAX_OUTPUT_TOKENS}; raise it or stream "
                f"if you need a longer reply]"
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        conversation.append({"role": "assistant", "content": text})

        print(f"\nClaude: {text}\n")

        u = response.usage
        print(
            f"  [tokens — input: {u.input_tokens}, output: {u.output_tokens}, "
            f"cache_read: {u.cache_read_input_tokens}, "
            f"cache_write: {u.cache_creation_input_tokens}]\n"
        )


if __name__ == "__main__":
    run()


# ---------------------------------------------------------------------------
# Streaming variant — uncomment if your replies routinely exceed ~16K tokens
# (non-streaming hits SDK HTTP timeouts above that). Otherwise non-streaming
# is simpler.
#
#     with client.messages.stream(**request) as stream:
#         for text in stream.text_stream:
#             print(text, end="", flush=True)
#         response = stream.get_final_message()
# ---------------------------------------------------------------------------
