from __future__ import annotations

from typing import Any

from figma_exporter.client import FigmaClient


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        text: str = "",
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text
        self.content = content

    def json(self) -> dict[str, Any]:
        return self._json_data


class SequenceSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "params": params or {}})
        return self.responses.pop(0)


def test_rate_limit_retry_uses_retry_after_with_sixty_second_cap() -> None:
    session = SequenceSession(
        [
            FakeResponse(
                429,
                json_data={"err": "too many requests"},
                headers={"Retry-After": "120", "X-Figma-Rate-Limit-Type": "low"},
            ),
            FakeResponse(json_data={"name": "Demo"}),
        ],
    )
    sleeps: list[float] = []
    client = FigmaClient("token", session=session, sleep=sleeps.append)

    assert client.get_file("demo") == {"name": "Demo"}
    assert sleeps == [60.0]
    assert len(session.calls) == 2
