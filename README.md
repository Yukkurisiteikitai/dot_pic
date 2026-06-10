# AI Pixel Cleaner

AI Pixel Cleaner turns AI-generated images into low-resolution, limited-palette,
editable pixel-art source material.

It does not try to fully redraw the artwork. The first goal is to produce files
that are easy to inspect and repair in tools such as Aseprite.

## Setup

```sh
uv venv .venv
uv sync
```

## CLI

```sh
uv run ai-pixel-cleaner input.png --size 128 --colors 32 --cleanup normal
```

Default outputs are written to `outputs/`:

- `fixed.png`: indexed PNG for hand editing
- `preview_4x.png`: nearest-neighbor preview
- `palette.png`: palette swatches
- `palette.hex`: palette colors as hex values
- `problem_map.png`: red overlay for likely problem pixels
- `report.json`: machine-readable diagnostics

Useful options:

```sh
uv run ai-pixel-cleaner input.png -o outputs/demo --size 96 --colors 24
uv run ai-pixel-cleaner input.png --fit cover --cleanup weak
uv run ai-pixel-cleaner input.png --no-outline-preserve --cleanup strong
```

## Web UI

```sh
uv run streamlit run app.py
```

The UI exposes target size, color count, cleanup strength, fit mode, outline
preservation, and preview scale.
