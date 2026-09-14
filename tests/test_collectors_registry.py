from radar.collectors import registry
from radar.collectors.base import Collector, CollectorError, RawItem


def _make_run(conn) -> int:
    cur = conn.execute(
        "INSERT INTO system_runs (run_type, started_at) VALUES ('manual', '2026-09-14T00:00:00')"
    )
    conn.commit()
    return cur.lastrowid


def test_run_collection_sample_mode_inserts_items(conn):
    registry.seed_sources = registry.seed_sources if hasattr(registry, "seed_sources") else None
    from radar.db.connection import seed_sources

    seed_sources(conn)
    run_id = _make_run(conn)
    summary = registry.run_collection(conn, run_id, use_sample=True)
    assert summary["items_collected"] == 10
    assert summary["duplicates_found"] == 0
    row_count = conn.execute("SELECT COUNT(*) AS n FROM raw_items").fetchone()["n"]
    assert row_count == 10


def test_run_collection_sample_mode_is_idempotent(conn):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    run_id = _make_run(conn)
    registry.run_collection(conn, run_id, use_sample=True)
    second = registry.run_collection(conn, run_id, use_sample=True)
    assert second["items_collected"] == 0
    assert second["duplicates_found"] == 10


class _AlwaysFails(Collector):
    method = "rss"

    def collect(self, source):
        raise CollectorError("simulated dead source")


class _AlwaysWorks(Collector):
    method = "api"

    def collect(self, source):
        return [RawItem(source_id=source["id"], title="A working item title here", url="https://example.com/a")]


class _FailsOnceThenWorks(Collector):
    method = "pagewatch"

    def __init__(self):
        self.calls = 0

    def collect(self, source):
        self.calls += 1
        if self.calls == 1:
            raise CollectorError("transient failure")
        return [RawItem(source_id=source["id"], title="Recovered after retry", url="https://example.com/b")]


def test_failed_source_is_logged_and_does_not_stop_other_sources(conn, monkeypatch):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "rss", _AlwaysFails())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "api", _AlwaysWorks())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "pagewatch", _AlwaysWorks())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "cbic_api", _AlwaysWorks())
    run_id = _make_run(conn)

    summary = registry.run_collection(conn, run_id, use_sample=False)

    assert len(summary["errors"]) > 0
    failures = conn.execute("SELECT COUNT(*) AS n FROM source_failures").fetchone()["n"]
    assert failures > 0
    # api/pagewatch sources still collected despite rss sources failing
    assert summary["items_collected"] > 0


def test_collect_with_retries_recovers_transient_failure():
    flaky = _FailsOnceThenWorks()
    items = registry._collect_with_retries(flaky, {"id": "some_source"})
    assert flaky.calls == 2  # failed once, retried, succeeded
    assert len(items) == 1
    assert items[0].title == "Recovered after retry"


def test_collect_with_retries_raises_after_exhausting_retries():
    always_fails = _AlwaysFails()
    try:
        registry._collect_with_retries(always_fails, {"id": "some_source"})
        assert False, "expected CollectorError"
    except CollectorError:
        pass


def test_run_collection_retries_before_logging_a_failure(conn, monkeypatch):
    from radar.db.connection import seed_sources

    seed_sources(conn)
    # Only one active source uses the "pagewatch" method's flaky instance;
    # everything else is swapped for a collector that always works so the
    # run's overall failure count reflects only the flaky source's behaviour.
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "pagewatch", _FailsOnceThenWorks())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "rss", _AlwaysWorks())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "api", _AlwaysWorks())
    monkeypatch.setitem(registry._COLLECTORS_BY_METHOD, "cbic_api", _AlwaysWorks())
    run_id = _make_run(conn)

    summary = registry.run_collection(conn, run_id, use_sample=False)

    # Each pagewatch source gets its own retry budget (MAX_RETRIES=2), so a
    # collector that fails only on its very first-ever call recovers on
    # every source's first attempt after that — no source_failures logged.
    failures = conn.execute("SELECT COUNT(*) AS n FROM source_failures").fetchone()["n"]
    assert failures == 0
    assert summary["items_collected"] > 0
