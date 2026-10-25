import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from obtener_catalogo_colonial import Download
from precios_supermercados.scrapers.colonial import ORIGIN, ColonialError


def downloader(tmp_path, monkeypatch, statuses, budget=5):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs == {"timeout": 20, "allow_redirects": False}
        return SimpleNamespace(status_code=statuses.pop(0), content=b'{"products":[]}')
    monkeypatch.setattr("requests.Session.get", lambda self, *a, **kw: get(*a, **kw))
    monkeypatch.setattr("obtener_catalogo_colonial.time.sleep", lambda seconds: None)
    obj = Download(tmp_path / "capture", datetime.now(UTC) + timedelta(minutes=1), [], budget, 60)
    return obj, calls


def test_deduplication_and_budget_before_network(tmp_path, monkeypatch):
    obj, calls = downloader(tmp_path, monkeypatch, [200], budget=1)
    assert obj.get(ORIGIN + "/x") == obj.get(ORIGIN + "/x")
    with pytest.raises(ColonialError, match="budget"):
        obj.get(ORIGIN + "/y")
    assert calls == [ORIGIN + "/x"]
    assert obj.metrics["duplicate_requests_avoided"] == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_controls_stop_without_retry(tmp_path, monkeypatch, status):
    obj, calls = downloader(tmp_path, monkeypatch, [status, 200])
    with pytest.raises(ColonialError, match="source_request_failed"):
        obj.get(ORIGIN + "/x")
    assert len(calls) == 1 and obj.metrics["retries"] == 0


def test_transient_retry_is_bounded_and_recorded(tmp_path, monkeypatch):
    obj, calls = downloader(tmp_path, monkeypatch, [503, 200])
    obj.get(ORIGIN + "/x")
    assert len(calls) == 2
    assert obj.metrics["retries"] == 1 and obj.metrics["failed_requests"] == 1
    assert [r["status"] for r in obj.records] == [503, 200]


def test_offline_never_fetches_missing_resource(tmp_path, monkeypatch):
    obj, calls = downloader(tmp_path, monkeypatch, [200])
    obj.offline = True
    with pytest.raises(ColonialError, match="offline_cache_miss"):
        obj.get(ORIGIN + "/x")
    assert calls == []


def test_expired_authorization_or_wrong_origin_does_not_fetch(tmp_path, monkeypatch):
    obj, calls = downloader(tmp_path, monkeypatch, [200])
    with pytest.raises(ColonialError, match="origin"):
        obj.get("https://example.com/x")
    obj.until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ColonialError, match="expired"):
        obj.get(ORIGIN + "/x")
    assert calls == []
