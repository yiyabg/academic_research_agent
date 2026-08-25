"""Backward-compatible wrapper for the provider-neutral LLM verifier."""

import asyncio

from verify_llm_connectivity import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
