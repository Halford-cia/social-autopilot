import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from social.api import RemoteError
from social.claude import HAIKU, SONNET, Claude, RunLimit
from social.runtime import State

NOW = datetime(2026, 11, 30, tzinfo=UTC)
CONFIG = {"monthlySpendLimitCents": 1000, "maxTokensPerRun": 16000}


def response(model=SONNET, used_in=20, used_out=10):
    return dict(
        model=model,
        stop_reason="end_turn",
        usage=dict(input_tokens=used_in, output_tokens=used_out),
        content=[dict(type="text", text='"fixture"')],
    )


class ClaudeContract(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = self.enterContext(State(self.root))
        self.send = Mock(return_value=response())
        self.keys = self.enterContext(patch("social.api.subprocess.run"))
        self.keys.return_value = Mock(returncode=0, stdout="fixture-from-keychain\n")
        self.enterContext(patch.dict(os.environ, ANTHROPIC_API_KEY="fixture-from-env"))
        self.client = self.make()

    def make(self, cfg=CONFIG, when=NOW):
        return Claude(cfg, self.store, send=self.send, clock=lambda: when)

    def test_messages_contract_and_keychain_only(self):
        self.assertEqual(self.client.generate(SONNET, "role", "prompt", 200), '"fixture"')
        (method, url, headers, data), _ = self.send.call_args
        self.assertEqual((method, url), ("POST", "https://api.anthropic.com/v1/messages"))
        self.assertEqual(headers["x-api-key"], "fixture-from-keychain")
        self.assertIn("anthropic-version", headers)
        self.assertIn("social-autopilot/ANTHROPIC_API_KEY", self.keys.call_args.args[0])
        self.assertEqual(json.loads(data)["messages"], [dict(role="user", content="prompt")])
        self.assertEqual(json.loads(data)["model"], "claude-sonnet-5-5")
        self.assertEqual(json.loads(data)["max_tokens"], 200)
        self.assertEqual(self.store.data["anthropicSpendNanoUsd"]["2026-11"], 140000)
        self.assertNotIn("fixture", self.store.file.read_text())

    def test_env_alone_cannot_authorize_request(self):
        self.keys.return_value = Mock(returncode=44, stdout="")
        with self.assertRaises(ValueError):
            self.client.generate(HAIKU, "role", "text", 100)
        self.send.assert_not_called()

    def test_durable_reservation_retained_on_unknown_outcome(self):
        def fail(*_):
            self.assertGreater(
                json.loads(self.store.file.read_text())["anthropicSpendNanoUsd"]["2026-11"], 0
            )
            raise RemoteError()

        self.send.side_effect = fail
        with self.assertRaises(RemoteError):
            self.client.generate(SONNET, "role", "prompt", 100)
        held = self.store.data["anthropicSpendNanoUsd"]["2026-11"]
        self.assertGreaterEqual(held, 100 * 10000)
        self.assertNotIn("prompt", self.store.file.read_text())

    def test_limits_apply_across_models_and_stop_before_billable_request(self):
        self.client = self.make(CONFIG | {"maxTokensPerRun": 1000})
        self.send.return_value = response(HAIKU, 85, 90)
        self.client.generate(HAIKU, "role", "text", 100)
        with self.assertRaises(RunLimit):
            self.client.generate(SONNET, "role", "text", 825)
        self.assertEqual(self.send.call_count, 1)
        self.store.data["anthropicSpendNanoUsd"]["2026-11"] = 1000 * 10_000_000
        with self.assertRaises(RunLimit):
            self.make().check_limit()
        self.make(when=datetime(2026, 12, 1, tzinfo=UTC)).check_limit()

    def test_reject_if_reservation_would_cross_monthly_limit(self):
        self.store.data["anthropicSpendNanoUsd"] = {"2026-11": 9_900_000}
        with self.assertRaises(RunLimit):
            self.make(CONFIG | {"monthlySpendLimitCents": 1}).generate(SONNET, "role", "p", 100)
        self.send.assert_not_called()

    def test_truncation_is_charged_and_never_returned(self):
        self.send.return_value = response() | {"stop_reason": "max_tokens"}
        with self.assertRaises(ValueError):
            self.client.generate(SONNET, "role", "p", 200)
        self.assertEqual(self.store.data["anthropicSpendNanoUsd"]["2026-11"], 140000)

    def test_ambiguous_request_reaching_token_cap_stops_run_immediately(self):
        # First observe the reservation made by this exact request on the wire.
        self.send.side_effect = RemoteError()
        with self.assertRaises(RemoteError):
            self.client.generate(HAIKU, "short role", "question", 100)
        capped = self.make(CONFIG | {"maxTokensPerRun": self.client.tokens})
        with self.assertRaises(RunLimit):
            capped.generate(HAIKU, "short role", "question", 100)

    def test_broken_usage_receipt_retains_reservation_and_stops(self):
        for malformed in (None, [], {"usage": None}, {"usage": {"input_tokens": -1}}):
            self.send.return_value = malformed
            with self.assertRaises(RunLimit):
                self.client.generate(SONNET, "role", "q", 100)

    def test_unlock_spanning_midnight_charges_the_month_of_dispatch(self):
        clock = Mock(return_value=NOW)

        def unlock(*_, **kwargs):
            clock.return_value = datetime(2026, 12, 1, tzinfo=UTC)
            return Mock(returncode=0, stdout="fixture")

        self.keys.side_effect = unlock
        client = Claude(CONFIG, self.store, send=self.send, clock=clock)
        client.generate(SONNET, "role", "p", 100)
        self.assertEqual(self.store.data["anthropicSpendNanoUsd"], {"2026-12": 140000})
