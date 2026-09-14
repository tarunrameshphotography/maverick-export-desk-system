from radar import settings
from radar.pipeline import exposure_lookup


def test_no_hs_code_gives_a_clear_cannot_look_up_message():
    line = exposure_lookup.exposure_line([], ["United States"])
    assert "No HS code extracted yet" in line


def test_without_api_key_falls_back_to_manual_lookup_link(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", None)
    line = exposure_lookup.exposure_line(["6109"], ["United States"])
    assert "6109" in line
    assert "United States" in line
    assert exposure_lookup.COMTRADE_SEARCH_URL in line
    assert exposure_lookup.TRADESTAT_URL in line
    assert "$" not in line  # never a fabricated number


def test_unmapped_destination_falls_back_even_with_a_key(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", "fake-key")
    line = exposure_lookup.exposure_line(["6109"], ["Bhutan"])  # not in COUNTRY_M49
    assert exposure_lookup.COMTRADE_SEARCH_URL in line


def test_falls_through_to_a_later_destination_that_is_mapped(monkeypatch):
    # The first extracted destination has no M49 entry; a later one does and
    # should still be tried automatically rather than giving up early.
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"primaryValue": 1234.0, "period": "2026"}]}

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert params["partnerCode"] == exposure_lookup.COUNTRY_M49["Vietnam"]
        return _FakeResponse()

    monkeypatch.setattr(settings, "COMTRADE_API_KEY", "fake-key")
    monkeypatch.setattr(exposure_lookup.requests, "get", _fake_get)
    line = exposure_lookup.exposure_line(["6109"], ["Bhutan", "Vietnam"])
    assert "Vietnam" in line
    assert "$1,234" in line


def test_newly_covered_destination_takes_the_manual_link_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", None)
    line = exposure_lookup.exposure_line(["6109"], ["Vietnam"])
    assert "Vietnam" in line
    assert exposure_lookup.COMTRADE_SEARCH_URL in line
    assert "$" not in line


def test_no_destination_market_still_gives_a_usable_manual_link(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", None)
    line = exposure_lookup.exposure_line(["6109"], [])
    assert "6109" in line
    assert exposure_lookup.COMTRADE_SEARCH_URL in line


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_successful_comtrade_lookup_produces_a_real_figure(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", "fake-key")
    monkeypatch.setattr(
        exposure_lookup.requests, "get",
        lambda *a, **k: _Resp({"data": [{"primaryValue": 12345678.9, "period": "2026"}]}),
    )
    line = exposure_lookup.exposure_line(["6109"], ["United States"])
    assert "$12,345,679" in line
    assert "2026" in line
    assert "UN Comtrade, auto-retrieved" in line


def test_comtrade_failure_falls_back_to_manual_link(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", "fake-key")

    def _boom(*a, **k):
        raise __import__("requests").RequestException("timeout")

    monkeypatch.setattr(exposure_lookup.requests, "get", _boom)
    line = exposure_lookup.exposure_line(["6109"], ["United States"])
    assert exposure_lookup.COMTRADE_SEARCH_URL in line


def test_comtrade_empty_data_falls_back_to_manual_link(monkeypatch):
    monkeypatch.setattr(settings, "COMTRADE_API_KEY", "fake-key")
    monkeypatch.setattr(exposure_lookup.requests, "get", lambda *a, **k: _Resp({"data": []}))
    line = exposure_lookup.exposure_line(["6109"], ["United States"])
    assert exposure_lookup.COMTRADE_SEARCH_URL in line
