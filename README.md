# figma-exporter

Python CLI for exporting one Figma file into a local offline cache that can be
committed to git, inspected without Figma access, and passed to an AI agent.

## What It Exports

- Full document tree from `GET /v1/files/:key` to `document.json`.
- PNG renders for every top-level node on every page, excluding `SLICE`.
- SVG renders for the same top-level nodes.
- Embedded image fills from `GET /v1/files/:key/images`.
- Published styles from `GET /v1/files/:key/styles`.
- Local variables from `GET /v1/files/:key/variables/local` when available.
- `manifest.json` and `index.md` navigation files that link design nodes to
  files on disk.

Pages themselves are not rendered as images. The Figma API renders frames,
groups, components, and similar nodes, so each page is represented by its
top-level renderable children plus the full tree in `document.json`.

## Installation

```bash
python -m pip install -e .
```

For development and tests:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

## Usage

Create a Figma personal access token with at least `file_content:read`. Add
`file_variables:read` if you need variables and your plan supports that
endpoint.

```bash
export FIGMA_TOKEN="figd_..."
figma-exporter FILE_KEY valta_be_cache
```

You can also pass a Figma file/design URL instead of the raw key:

```bash
figma-exporter "https://www.figma.com/design/FILE_KEY/Name" figma_cache
```

Useful options:

```bash
figma-exporter FILE_KEY output_dir --png-scale 2 --batch-size 50
figma-exporter FILE_KEY output_dir --skip-variables
```

## Output Layout

```text
output_dir/
  document.json
  manifest.json
  index.md
  styles.json
  variables.json
  png/<page>/<node>__<id>.png
  svg/<page>/<node>__<id>.svg
  image_fills/<ref>.png
```

The exporter writes files idempotently, so repeating a run overwrites the same
paths with the latest Figma snapshot.

## API Limits And Failures

Render requests are batched by 50 node IDs. If Figma returns `429`, the client
retries up to 8 times and respects `Retry-After`, capped at 60 seconds per
wait.

The variables endpoint is optional. `403` and `404` from
`/files/:key/variables/local` do not fail the export; the error is recorded in
`manifest.json` and the rest of the cache is still written.
