"""Text-only Claude Messages requests, accounted under the Mini's exclusive state lock."""

import json
from datetime import UTC, datetime

from .api import RemoteError, request, secret

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
# Integer nanodollars per token at standard global rates for short, uncached requests.
RATES = {SONNET: (2000, 10000), HAIKU: (100, 500)}


class RunLimit(RuntimeError):
    """Abort the entire tick, including work for the other brand."""


def validate_limits(limits):
    for key in ("maxTokensPerRun", "monthlySpendLimitCents"):
        if type(limits.get(key)) is not int or limits[key] < 0:
            raise ValueError("Configure nonnegative integer Claude limits")


class Claude:
    def __init__(self, limits, store, *, send=request, clock=lambda: datetime.now(UTC)):
        validate_limits(limits)
        self.token_limit = limits["maxTokensPerRun"]
        self.month_limit = limits["monthlySpendLimitCents"] * 10_000_000
        self.store = store
        self.send = send
        self.clock = clock
        self.tokens = 0
        self.months = store.data.setdefault("anthropicSpendNanoUsd", {})

    def check_limit(self):
        month = self.clock().astimezone(UTC).strftime("%Y-%m")
        used = self.months.get(month, 0)
        if type(used) is not int or used < 0:
            raise RunLimit("Review the monthly Claude ledger")
        if used >= self.month_limit or self.tokens >= self.token_limit:
            raise RunLimit("Claude budget reached")
        return month

    def generate(self, model, system, prompt, max_tokens):
        self.check_limit()
        input_rate, output_rate = RATES[model]
        body = json.dumps(
            {
                "model": model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "output_config": {"effort": "low"},
                "service_tier": "standard_only",
                "inference_geo": "global",
            }
        ).encode()
        # Bound our two fixed-format, text-only prompts at one token per serialized byte
        # plus framing headroom. Keep every request in Haiku's short-context price tier.
        input_bound = len(body) + 256
        if input_bound > 100_000 or type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("Unsupported Claude request size")
        reserved_tokens = input_bound + max_tokens
        reserved_cost = input_bound * input_rate + max_tokens * output_rate
        if self.tokens + reserved_tokens > self.token_limit:
            raise RunLimit("Claude request exceeds remaining token budget")

        key = secret("ANTHROPIC_API_KEY")
        # A Keychain unlock prompt can span midnight. Reserve against the dispatch month.
        month = self.check_limit()
        used = self.months.get(month, 0)
        if used + reserved_cost > self.month_limit:
            raise RunLimit("Claude request exceeds remaining monthly budget")
        headers = {"x-api-key": key, "anthropic-version": "2026-10-21", "Content-Type": "application/json"}
        self.tokens += reserved_tokens
        self.months[month] = used + reserved_cost
        self.store.save()  # An interrupted or ambiguous request keeps its entire reservation.
        try:
            result = self.send("POST", "https://api.anthropic.com/v1/messages", headers, body)
        except RemoteError as exc:
            if exc.status in (400, 401, 403, 404, 413, 429):
                self.tokens -= reserved_tokens
                self.months[month] = used
                self.store.save()
            self.check_limit()
            raise

        if not isinstance(result, dict) or not isinstance(result.get("usage"), dict):
            raise RunLimit("Missing Claude usage; reservation retained")
        usage = result["usage"]
        in_tokens, out_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        if (
            result.get("model") != model
            or any(type(n) is not int or n < 0 for n in (in_tokens, out_tokens))
            or usage.get("cache_creation_input_tokens")
            or usage.get("cache_read_input_tokens")
        ):
            raise RunLimit("Unrecognized Claude usage; reservation retained")
        self.tokens += in_tokens + out_tokens - reserved_tokens
        self.months[month] = used + in_tokens * input_rate + out_tokens * output_rate
        self.store.save()
        if in_tokens > input_bound or out_tokens > max_tokens:
            raise RunLimit("Claude usage exceeded its reservation")
        self.check_limit()
        texts = [block["text"] for block in result.get("content", []) if block.get("type") == "text"]
        if result.get("stop_reason") != "end_turn" or len(texts) != 1 or not isinstance(texts[0], str):
            raise ValueError("Incomplete Claude output")
        return texts[0].strip()
