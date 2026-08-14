from __future__ import annotations

from kitchen import prep_chef


class _Response:
    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        return self._payload


def test_run_once_polls_new_orders_before_resolving_and_dispatches_both(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def request(method: str, url: str, **kwargs: object) -> _Response:
        params = kwargs.get("params")
        calls.append((method, url, params if isinstance(params, dict) else None))
        if (
            method == "GET"
            and isinstance(params, dict)
            and params.get("processing_status") == "new"
        ):
            return _Response(200, [{"id": 101, "req_id": "req-new", "processing_status": "new"}])
        if (
            method == "GET"
            and isinstance(params, dict)
            and params.get("processing_status") == "resolving"
        ):
            return _Response(
                200,
                [
                    {
                        "id": 202,
                        "req_id": "req-resolving",
                        "processing_status": "resolving",
                    }
                ],
            )
        return _Response(200, {"status": "dispatched"})

    monkeypatch.setattr(prep_chef.service_helpers, "request_control_plane_sync", request)

    prep_chef._run_once(loop_limit=5)

    assert calls[0][0] == "GET"
    assert calls[0][2] == {"processing_status": "new", "limit": 5}
    assert calls[1][0] == "GET"
    assert calls[1][2] == {"processing_status": "resolving", "alert_status": "resolved", "limit": 5}
    assert calls[2][0] == "POST"
    assert calls[2][1].endswith("/cook/orders/101")
    assert calls[3][0] == "POST"
    assert calls[3][1].endswith("/cook/orders/202")


def test_run_once_dedupes_orders_seen_in_both_poll_results(monkeypatch) -> None:
    posts: list[str] = []

    def request(method: str, url: str, **kwargs: object) -> _Response:
        params = kwargs.get("params")
        if (
            method == "GET"
            and isinstance(params, dict)
            and params.get("processing_status") == "new"
        ):
            return _Response(200, [{"id": 101, "req_id": "req-shared", "processing_status": "new"}])
        if (
            method == "GET"
            and isinstance(params, dict)
            and params.get("processing_status") == "resolving"
        ):
            return _Response(
                200,
                [{"id": 101, "req_id": "req-shared", "processing_status": "resolving"}],
            )
        if method == "POST":
            posts.append(url)
        return _Response(200, {"status": "dispatched"})

    monkeypatch.setattr(prep_chef.service_helpers, "request_control_plane_sync", request)

    prep_chef._run_once(loop_limit=5)

    assert posts == [f"{prep_chef.API_URL}/cook/orders/101"]
