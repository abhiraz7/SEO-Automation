"""
Regression: 2026-10-01 incident -- every suggestion/meta/image-alt generation
through Claude started failing in prod with

    suggestions.provider_failed project=... error=TypeError
    TypeError: Messages.create() got an unexpected keyword argument 'temperature'

Cause: requirements.txt pinned `anthropic` with no version. anthropic 1.x
(released after this code was written) removed `temperature` as a direct
keyword argument of Messages.create() -- it's no longer in the method's
Python signature at all, so passing it is a TypeError raised before any
network call, not an API error. app/claude.py's _complete() and
image_alt_completion() both called .messages.create(..., temperature=...)
directly.

A plain unittest.mock.Mock() would NOT catch this -- it silently accepts
any keyword argument. This fake reproduces the real SDK's actual (narrower)
signature instead, so calling with an argument that signature doesn't
accept fails exactly the way the real SDK does.
"""
from unittest.mock import patch

from app import claude


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    """anthropic>=1.0's real Messages.create signature: no `temperature`
    keyword -- only `extra_body` can carry it."""

    def create(self, *, model, max_tokens, messages, system=None, extra_body=None):
        return _FakeMessage("ok")


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_complete_does_not_pass_temperature_directly_to_messages_create():
    with patch.object(claude, "_get_client", return_value=_FakeClient()):
        result = claude._complete("prompt", max_tokens=100, temperature=0.7)
    assert result == "ok"


def test_image_alt_completion_does_not_pass_temperature_directly_either():
    with patch.object(claude, "_get_client", return_value=_FakeClient()):
        result = claude.image_alt_completion("desc", b"fakebytes", "image/png", temperature=0.4)
    assert result == "ok"
