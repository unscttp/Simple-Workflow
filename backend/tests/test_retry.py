"""Automatic retries of model requests: classification, client setup, the switch."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

import anthropic
import httpx
import openai
from workflow_core import TransientModelError as NLGTransient

from app.providers import PROVIDERS, ModelError, TransientModelError, create_model
from app.providers.anthropic_messages import _wrap_errors
from app.providers.chat_completions import wrap_openai_errors

from .support import ApiCase, answer

REQUEST = httpx.Request("POST", "https://api.example.com")


def status_error(module, status):
    return module.APIStatusError("x", response=httpx.Response(status, request=REQUEST), body=None)


def raising(exc):
    def call():
        raise exc

    return call


class ClassificationTests(unittest.TestCase):
    def test_openai_errors(self):
        for exc, transient in [
            (openai.APITimeoutError(request=REQUEST), True),
            (openai.APIConnectionError(request=REQUEST), True),
            (status_error(openai, 429), True),
            (status_error(openai, 503), True),
            (status_error(openai, 400), False),
            (status_error(openai, 401), False),
        ]:
            with self.subTest(exc=type(exc).__name__), self.assertRaises(ModelError) as caught:
                wrap_openai_errors(raising(exc))
            self.assertEqual(isinstance(caught.exception, TransientModelError), transient)
            self.assertEqual(isinstance(caught.exception, NLGTransient), transient)
        with self.assertRaisesRegex(ModelError, "timed out"):
            wrap_openai_errors(raising(openai.APITimeoutError(request=REQUEST)))

    def test_anthropic_errors(self):
        with self.assertRaises(TransientModelError):
            _wrap_errors(raising(anthropic.APIConnectionError(request=REQUEST)))
        with self.assertRaises(TransientModelError):
            _wrap_errors(raising(status_error(anthropic, 529)))  # overloaded
        with self.assertRaises(ModelError) as caught:
            _wrap_errors(raising(status_error(anthropic, 400)))
        self.assertNotIsInstance(caught.exception, TransientModelError)

    def test_clients_leave_retries_to_nlg_and_wait_for_long_answers(self):
        provider = replace(PROVIDERS["deepseek"], timeout_seconds=420)
        client = create_model(provider, "key", "m")._client
        self.assertEqual(client.max_retries, 0)
        self.assertEqual((client.timeout.read, client.timeout.connect), (420, 15))
        claude = create_model(PROVIDERS["anthropic"], "key", "m")._client
        self.assertEqual((claude.max_retries, claude.timeout.read), (0, 300))


class FlakyModel:
    """Times out ``failures`` times, then answers."""

    provider, model = "scripted", "flaky"

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def decide(self, turn):
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientModelError("Model request failed: the request timed out.")
        return answer("好的")

    def complete_json(self, system, user, schema):
        return {"operations": [], "rationale": "", "evidence": []}


class SwitchTests(ApiCase):
    def setUp(self):
        super().setUp()
        sleeper = mock.patch("workflow_model_retry._sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def factory(self, provider, api_key, model):
        return self.flaky

    def test_retries_are_on_by_default(self):
        self.flaky = FlakyModel(failures=2)
        turn = self.send("你好")
        self.assertEqual((turn["status"], turn["answer"]), ("completed", "好的"))
        self.assertEqual(turn["stats"]["retries"], 2)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 4])
        view = self.client.get("/api/providers").json()["auto_retry"]
        self.assertEqual(view, {"default": True, "attempts": 3})

    def test_retries_can_be_switched_off_per_request(self):
        self.flaky = FlakyModel(failures=1)
        turn = self.send("你好", auto_retry=False)
        self.assertEqual(turn["status"], "failed")
        self.assertIn("timed out", turn["error"])
        self.assertEqual((self.flaky.calls, turn["stats"]["retries"]), (1, 0))

    def test_retries_give_up_after_the_configured_attempts(self):
        self.flaky = FlakyModel(failures=10)
        turn = self.send("你好")
        self.assertEqual(turn["status"], "failed")
        self.assertEqual((self.flaky.calls, turn["stats"]["retries"]), (4, 3))


if __name__ == "__main__":
    unittest.main()
