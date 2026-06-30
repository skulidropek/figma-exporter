from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from figma_exporter.client import FigmaApiError, FigmaClient


@dataclass
class RenderTarget:
    node_id: str
    entry: dict[str, Any]
    png_path: Path
    svg_path: Path


class FigmaExporter:
    """Export one Figma file into a local, versionable cache directory."""

    def __init__(
        self,
        client: FigmaClient,
        output_dir: str | Path,
        *,
        png_scale: int | float = 2,
        batch_size: int = 50,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.client = client
        self.output_dir = Path(output_dir)
        self.png_scale = png_scale
        self.batch_size = batch_size
        self.clock = clock or utc_now

    def export_file(self, file_key: str, *, include_variables: bool = True) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        document_payload = self.client.get_file(file_key)
        write_json(self.output_dir / "document.json", document_payload)

        manifest, render_targets = self._build_manifest(file_key, document_payload)
        self._render_nodes(file_key, render_targets, manifest)
        self._export_image_fills(file_key, manifest)
        self._export_styles(file_key, manifest)
        if include_variables:
            self._export_variables(file_key, manifest)
        else:
            manifest["variables"] = {"status": "skipped"}

        write_json(self.output_dir / "manifest.json", manifest)
        write_text(self.output_dir / "index.md", render_index(manifest))
        return manifest

    def _build_manifest(
        self,
        file_key: str,
        document_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], list[RenderTarget]]:
        document = document_payload.get("document") or {}
        file_name = document_payload.get("name") or document.get("name") or file_key
        manifest: dict[str, Any] = {
            "file_key": file_key,
            "file_name": file_name,
            "generated_at": self.clock(),
            "document": {"path": "document.json"},
            "pages": [],
            "image_fills": [],
            "styles": {"status": "pending"},
            "variables": {"status": "pending"},
            "errors": [],
        }
        render_targets: list[RenderTarget] = []

        for page in document.get("children") or []:
            page_id = str(page.get("id") or "")
            page_name = str(page.get("name") or page_id or "Page")
            page_dir = sanitize_filename(page_name, "page")
            page_entry: dict[str, Any] = {
                "id": page_id,
                "name": page_name,
                "type": page.get("type", "CANVAS"),
                "nodes": [],
            }

            for node in page.get("children") or []:
                if node.get("type") == "SLICE":
                    continue
                node_id = str(node.get("id") or "")
                if not node_id:
                    continue
                node_name = str(node.get("name") or node_id)
                base_name = f"{sanitize_filename(node_name, 'node')}__{sanitize_filename(node_id, 'id')}"
                png_path = Path("png") / page_dir / f"{base_name}.png"
                svg_path = Path("svg") / page_dir / f"{base_name}.svg"
                node_entry: dict[str, Any] = {
                    "id": node_id,
                    "name": node_name,
                    "type": node.get("type", "UNKNOWN"),
                    "paths": {
                        "png": png_path.as_posix(),
                        "svg": svg_path.as_posix(),
                    },
                    "renders": {},
                }
                page_entry["nodes"].append(node_entry)
                render_targets.append(RenderTarget(node_id, node_entry, png_path, svg_path))

            page_entry["node_count"] = len(page_entry["nodes"])
            manifest["pages"].append(page_entry)

        return manifest, render_targets

    def _render_nodes(
        self,
        file_key: str,
        render_targets: list[RenderTarget],
        manifest: dict[str, Any],
    ) -> None:
        render_specs = [
            ("png", self.png_scale, "png_path"),
            ("svg", None, "svg_path"),
        ]
        for image_format, scale, path_attr in render_specs:
            for batch in chunks(render_targets, self.batch_size):
                payload = self.client.get_rendered_image_urls(
                    file_key,
                    [target.node_id for target in batch],
                    image_format=image_format,
                    scale=scale,
                )
                api_error = payload.get("err")
                images = payload.get("images") or {}
                for target in batch:
                    relative_path = getattr(target, path_attr)
                    render_entry = target.entry["renders"].setdefault(image_format, {})
                    url = images.get(target.node_id)
                    if not url:
                        message = "Figma did not return a render URL"
                        if api_error:
                            message = f"{message}: {api_error}"
                        render_entry.update({"status": "missing_url", "error": message})
                        manifest["errors"].append(
                            {
                                "scope": "render",
                                "format": image_format,
                                "node_id": target.node_id,
                                "message": message,
                            },
                        )
                        continue
                    data = self.client.download(url)
                    write_bytes(self.output_dir / relative_path, data)
                    render_entry.update({"status": "saved", "path": relative_path.as_posix()})

    def _export_image_fills(self, file_key: str, manifest: dict[str, Any]) -> None:
        payload = self.client.get_image_fills(file_key)
        images = (payload.get("meta") or {}).get("images") or {}
        for image_ref, url in images.items():
            relative_path = Path("image_fills") / f"{sanitize_filename(str(image_ref), 'image_fill')}.png"
            entry: dict[str, Any] = {
                "ref": image_ref,
                "path": relative_path.as_posix(),
            }
            if url:
                write_bytes(self.output_dir / relative_path, self.client.download(str(url)))
                entry["status"] = "saved"
            else:
                entry["status"] = "missing_url"
                manifest["errors"].append(
                    {
                        "scope": "image_fill",
                        "ref": image_ref,
                        "message": "Figma did not return an image fill URL",
                    },
                )
            manifest["image_fills"].append(entry)

    def _export_styles(self, file_key: str, manifest: dict[str, Any]) -> None:
        styles = self.client.get_styles(file_key)
        write_json(self.output_dir / "styles.json", styles)
        manifest["styles"] = {
            "status": "saved",
            "path": "styles.json",
            "count": count_collection((styles.get("meta") or {}).get("styles")),
        }

    def _export_variables(self, file_key: str, manifest: dict[str, Any]) -> None:
        try:
            variables = self.client.get_variables(file_key)
        except FigmaApiError as exc:
            if exc.status_code not in {403, 404}:
                raise
            error = {
                "scope": "variables",
                "status_code": exc.status_code,
                "message": str(exc),
            }
            manifest["variables"] = {
                "status": "unavailable",
                "status_code": exc.status_code,
                "error": str(exc),
            }
            manifest["errors"].append(error)
            return

        write_json(self.output_dir / "variables.json", variables)
        meta = variables.get("meta") or {}
        manifest["variables"] = {
            "status": "saved",
            "path": "variables.json",
            "variables_count": count_collection(meta.get("variables") or variables.get("variables")),
            "collections_count": count_collection(
                meta.get("variableCollections") or variables.get("variableCollections"),
            ),
        }


def sanitize_filename(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    cleaned = re.sub(r"[^\w.-]+", "_", normalized, flags=re.UNICODE).strip("._ ")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:140]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def chunks(items: list[RenderTarget], size: int) -> Iterable[list[RenderTarget]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def count_collection(value: Any) -> int:
    if isinstance(value, (dict, list, tuple, set)):
        return len(value)
    return 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_index(manifest: dict[str, Any]) -> str:
    lines = [
        f"# {manifest['file_name']}",
        "",
        f"- File key: `{manifest['file_key']}`",
        f"- Document JSON: [{manifest['document']['path']}]({manifest['document']['path']})",
        f"- Manifest: [manifest.json](manifest.json)",
        "",
        "## Pages",
        "",
    ]

    for page in manifest["pages"]:
        lines.extend([f"### {page['name']}", ""])
        if not page["nodes"]:
            lines.extend(["No top-level renderable nodes.", ""])
            continue
        for node in page["nodes"]:
            png_path = node["paths"]["png"]
            svg_path = node["paths"]["svg"]
            lines.append(
                f"- `{node['type']}` {node['name']} (`{node['id']}`): "
                f"[PNG]({png_path}) · [SVG]({svg_path})",
            )
        lines.append("")

    lines.extend(["## Image Fills", ""])
    if manifest["image_fills"]:
        for image in manifest["image_fills"]:
            lines.append(f"- `{image['ref']}`: [{image['path']}]({image['path']})")
    else:
        lines.append("No image fills found.")
    lines.append("")

    styles = manifest["styles"]
    lines.extend(["## Styles", ""])
    if styles.get("status") == "saved":
        lines.append(f"- [{styles['path']}]({styles['path']}) ({styles['count']} styles)")
    else:
        lines.append(f"- {styles.get('status', 'unknown')}")
    lines.append("")

    variables = manifest["variables"]
    lines.extend(["## Variables", ""])
    if variables.get("status") == "saved":
        lines.append(
            f"- [{variables['path']}]({variables['path']}) "
            f"({variables['variables_count']} variables, "
            f"{variables['collections_count']} collections)",
        )
    elif variables.get("status") == "unavailable":
        lines.append(f"- Unavailable: {variables['error']}")
    else:
        lines.append(f"- {variables.get('status', 'unknown')}")

    if manifest["errors"]:
        lines.extend(["", "## Export Notes", ""])
        for error in manifest["errors"]:
            lines.append(f"- `{error['scope']}`: {error['message']}")

    return "\n".join(lines) + "\n"
