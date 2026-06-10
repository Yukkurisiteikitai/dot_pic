# AI Pixel Cleaner

AI Pixel Cleaner turns AI-generated images into low-resolution, limited-palette,
transparent sprite sheets that are easier to inspect and repair in tools such as
Aseprite.

It removes checker backgrounds, hardens alpha, reduces colors, and reassembles
the result into a clean PNG sheet.

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

- `sprite_sheet.png`: transparent sprite sheet
- `sprite_sheet_4x.png`: nearest-neighbor preview
- `frames/frame_001.png`: per-frame processed PNG
- `frames/frame_001_4x.png`: per-frame preview
- `palette.png`: palette swatches
- `palette.hex`: palette colors as hex values
- `problem_map.png`: red overlay for likely problem pixels
- `report.json`: machine-readable diagnostics

Useful options:

```sh
uv run ai-pixel-cleaner input.png -o outputs/demo --size 96 --colors 24
uv run ai-pixel-cleaner input.png --fit cover --cleanup weak
uv run ai-pixel-cleaner input.png --no-outline-preserve --cleanup strong
uv run ai-pixel-cleaner input.png --sheet-columns 4 --sheet-padding 1
```

## Web UI

```sh
uv run streamlit run app.py
```

The UI supports multiple uploads, checker background removal, alpha cleanup,
sprite sheet layout, and per-frame previews.
