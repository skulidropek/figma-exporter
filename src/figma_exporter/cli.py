from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from figma_exporter.client import FigmaClient
from figma_exporter.exporter import FigmaExporter


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("FIGMA_TOKEN")
    if not token:
        parser.error("Figma token is required. Set FIGMA_TOKEN or pass --token.")

    file_key = extract_file_key(args.file_key)
    if not file_key:
        parser.error("Could not extract a Figma file key from the provided value.")

    client = FigmaClient(token)
    exporter = FigmaExporter(
        client,
        args.output_dir,
        png_scale=args.png_scale,
        batch_size=args.batch_size,
    )
    manifest = exporter.export_file(
        file_key,
        include_variables=not args.skip_variables,
    )
    print(f"Exported {manifest['file_name']} to {Path(args.output_dir).resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a Figma file into a self-contained local cache.",
    )
    parser.add_argument(
        "file_key",
        help="Figma file key or a Figma file/design URL.",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="figma_cache",
        help="Directory for the exported cache. Defaults to figma_cache.",
    )
    parser.add_argument(
        "--token",
        help="Figma personal access token. Defaults to FIGMA_TOKEN.",
    )
    parser.add_argument(
        "--png-scale",
        type=float,
        default=2,
        help="Scale passed to the PNG render endpoint. Defaults to 2.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of node IDs per render request. Defaults to 50.",
    )
    parser.add_argument(
        "--skip-variables",
        action="store_true",
        help="Skip the optional local variables endpoint.",
    )
    return parser


def extract_file_key(value: str) -> str:
    value = value.strip()
    match = re.search(r"figma\.com/(?:file|design)/([^/?#]+)", value)
    if match:
        return match.group(1)
    if "/" in value or "?" in value or "#" in value:
        return ""
    return value
