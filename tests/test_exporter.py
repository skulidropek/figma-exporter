from __future__ import annotations

import json
from typing import Any, Callable

from figma_exporter.client import FigmaClient
from figma_exporter.exporter import FigmaExporter


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


class RoutingSession:
    def __init__(self, handler: Callable[[str, dict[str, Any]], FakeResponse]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> FakeResponse:
        params = params or {}
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.handler(url, params)


def test_exporter_writes_complete_cache_from_mocked_figma_api(tmp_path) -> None:
    document_payload = {
        "name": "Demo file",
        "document": {
            "id": "0:0",
            "name": "Document",
            "children": [
                {
                    "id": "0:1",
                    "name": "Main Page",
                    "type": "CANVAS",
                    "children": [
                        {"id": "1:2", "name": "Frame 1", "type": "FRAME"},
                        {"id": "1:3", "name": "Export slice", "type": "SLICE"},
                        {"id": "1:4", "name": "Photo/Card", "type": "GROUP"},
                    ],
                },
                {
                    "id": "0:2",
                    "name": "Empty",
                    "type": "CANVAS",
                    "children": [],
                },
            ],
        },
    }
    styles_payload = {"meta": {"styles": [{"key": "style1", "name": "Primary"}]}}
    variables_payload = {
        "meta": {
            "variables": {"var1": {"name": "Spacing"}},
            "variableCollections": {"col1": {"name": "Core"}},
        },
    }

    def handler(url: str, params: dict[str, Any]) -> FakeResponse:
        if url.endswith("/files/demo"):
            return FakeResponse(json_data=document_payload)
        if url.endswith("/images/demo"):
            ids = params["ids"].split(",")
            image_format = params["format"]
            return FakeResponse(
                json_data={
                    "images": {
                        node_id: f"https://download.local/{image_format}/{node_id}"
                        for node_id in ids
                    },
                },
            )
        if url.endswith("/files/demo/images"):
            return FakeResponse(
                json_data={
                    "meta": {"images": {"fill 1": "https://download.local/fill/1"}},
                },
            )
        if url.endswith("/files/demo/styles"):
            return FakeResponse(json_data=styles_payload)
        if url.endswith("/files/demo/variables/local"):
            return FakeResponse(json_data=variables_payload)
        if url.startswith("https://download.local/"):
            return FakeResponse(content=f"download:{url}".encode())
        raise AssertionError(f"Unexpected URL: {url}")

    session = RoutingSession(handler)
    client = FigmaClient("token", session=session)
    exporter = FigmaExporter(
        client,
        tmp_path / "cache",
        clock=lambda: "2026-06-29T00:00:00+00:00",
    )

    manifest = exporter.export_file("demo")

    output = tmp_path / "cache"
    assert (output / "document.json").is_file()
    assert (output / "styles.json").is_file()
    assert (output / "variables.json").is_file()
    assert (output / "manifest.json").is_file()
    assert (output / "index.md").is_file()
    assert (output / "png/Main_Page/Frame_1__1_2.png").read_bytes().startswith(
        b"download:",
    )
    assert (output / "svg/Main_Page/Photo_Card__1_4.svg").read_bytes().startswith(
        b"download:",
    )
    assert (output / "image_fills/fill_1.png").read_bytes().startswith(b"download:")

    assert manifest["file_name"] == "Demo file"
    assert manifest["styles"]["count"] == 1
    assert manifest["variables"]["variables_count"] == 1
    assert manifest["variables"]["collections_count"] == 1
    exported_node_ids = [
        node["id"]
        for page in manifest["pages"]
        for node in page["nodes"]
    ]
    assert exported_node_ids == ["1:2", "1:4"]

    saved_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["pages"][0]["nodes"][0]["paths"]["png"] == (
        "png/Main_Page/Frame_1__1_2.png"
    )


def test_render_requests_are_batched_by_fifty_node_ids(tmp_path) -> None:
    nodes = [
        {"id": f"1:{index}", "name": f"Node {index}", "type": "FRAME"}
        for index in range(51)
    ]
    document_payload = {
        "name": "Large file",
        "document": {
            "children": [
                {"id": "0:1", "name": "Main", "type": "CANVAS", "children": nodes},
            ],
        },
    }

    def handler(url: str, params: dict[str, Any]) -> FakeResponse:
        if url.endswith("/files/demo"):
            return FakeResponse(json_data=document_payload)
        if url.endswith("/images/demo"):
            ids = params["ids"].split(",")
            return FakeResponse(
                json_data={
                    "images": {
                        node_id: f"https://download.local/{params['format']}/{node_id}"
                        for node_id in ids
                    },
                },
            )
        if url.endswith("/files/demo/images"):
            return FakeResponse(json_data={"meta": {"images": {}}})
        if url.endswith("/files/demo/styles"):
            return FakeResponse(json_data={"meta": {"styles": []}})
        if url.endswith("/files/demo/variables/local"):
            return FakeResponse(
                json_data={
                    "meta": {"variables": {}, "variableCollections": {}},
                },
            )
        if url.startswith("https://download.local/"):
            return FakeResponse(content=b"asset")
        raise AssertionError(f"Unexpected URL: {url}")

    session = RoutingSession(handler)
    exporter = FigmaExporter(
        FigmaClient("token", session=session),
        tmp_path / "cache",
        batch_size=50,
    )

    exporter.export_file("demo")

    render_calls = [call for call in session.calls if call["url"].endswith("/images/demo")]
    batch_lengths = [len(call["params"]["ids"].split(",")) for call in render_calls]
    assert batch_lengths == [50, 1, 50, 1]
    assert [call["params"]["format"] for call in render_calls] == ["png", "png", "svg", "svg"]


def test_variables_permission_error_is_recorded_without_stopping_export(tmp_path) -> None:
    document_payload = {
        "name": "Demo file",
        "document": {
            "children": [
                {"id": "0:1", "name": "Main", "type": "CANVAS", "children": []},
            ],
        },
    }

    def handler(url: str, params: dict[str, Any]) -> FakeResponse:
        if url.endswith("/files/demo"):
            return FakeResponse(json_data=document_payload)
        if url.endswith("/files/demo/images"):
            return FakeResponse(json_data={"meta": {"images": {}}})
        if url.endswith("/files/demo/styles"):
            return FakeResponse(json_data={"meta": {"styles": []}})
        if url.endswith("/files/demo/variables/local"):
            return FakeResponse(403, json_data={"err": "forbidden"}, text="forbidden")
        raise AssertionError(f"Unexpected URL: {url}")

    session = RoutingSession(handler)
    exporter = FigmaExporter(
        FigmaClient("token", session=session),
        tmp_path / "cache",
    )

    manifest = exporter.export_file("demo")

    assert manifest["variables"]["status"] == "unavailable"
    assert manifest["variables"]["status_code"] == 403
    assert manifest["errors"][0]["scope"] == "variables"
    assert not (tmp_path / "cache" / "variables.json").exists()
