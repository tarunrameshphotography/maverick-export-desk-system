"""A minimal fake requests.Session for Phase 4 publisher tests -- no real
network calls. Responses are consumed in the order publish() makes calls,
so each test lists exactly the responses its call sequence needs."""
from __future__ import annotations


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body on this fake response")
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"FakeSession ran out of scripted responses at {method} {url}")
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._next("PUT", url, **kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)
