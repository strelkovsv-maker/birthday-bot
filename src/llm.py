"""Anthropic Claude API wrapper for generating birthday wishes."""
from __future__ import annotations

import logging
from typing import Iterable

from anthropic import Anthropic, APIError

from src.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class WishGenerator:
    """Thin wrapper around the Anthropic client for our specific use case."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate(
        self,
        name: str,
        department: str,
        role: str,
        notes: str,
        prior_drafts: Iterable[str] = (),
        *,
        max_tokens: int = 600,
        temperature: float = 0.85,
    ) -> str:
        """Generate a single birthday wish.

        Raises APIError on failure — caller decides whether to retry or fall back.
        """
        user_prompt = build_user_prompt(name, department, role, notes, prior_drafts)
        prior_count = len(list(prior_drafts)) if prior_drafts else 0
        logger.info(
            "Generating wish for %s (department=%s, role=%s, prior_drafts=%d)",
            name, department, role, prior_count,
        )

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except APIError:
            logger.exception("Anthropic API call failed")
            raise

        # Extract text. Anthropic returns a list of content blocks; for our
        # text-only use case, we expect a single text block.
        text_parts = [
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ]
        wish = "".join(text_parts).strip()

        if not wish:
            logger.warning("LLM returned empty content; raw response: %s", resp)
            raise RuntimeError("LLM returned empty wish text")

        logger.info(
            "Wish generated (%d chars, in_tokens=%d, out_tokens=%d)",
            len(wish),
            resp.usage.input_tokens,
            resp.usage.output_tokens,
        )
        return wish
