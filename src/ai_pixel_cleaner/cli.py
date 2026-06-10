from __future__ import annotations

import argparse
from pathlib import Path

from .core import CleanOptions, clean_image, parse_size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-pixel-cleaner",
        description="Convert an AI image into editable pixel-art source material.",
    )
    parser.add_argument("input", type=Path, help="Input image path.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="Directory for sprite_sheet.png, previews, palette, problem map, and report.",
    )
    parser.add_argument(
        "--size",
        default="128",
        help="Target canvas size, for example 64, 128, or 128x96.",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=32,
        help="Palette size for visible pixels.",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=4,
        help="Nearest-neighbor preview scale.",
    )
    parser.add_argument(
        "--cleanup",
        choices=["off", "weak", "normal", "strong"],
        default="normal",
        help="Isolated-pixel cleanup strength.",
    )
    parser.add_argument(
        "--fit",
        choices=["contain", "cover", "stretch"],
        default="contain",
        help="How to place the source image on the target canvas.",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=128,
        help="Alpha threshold for converting soft transparency to hard pixels.",
    )
    parser.add_argument(
        "--no-outline-preserve",
        action="store_true",
        help="Allow cleanup to modify pixels on transparent edges.",
    )
    parser.add_argument(
        "--background-tolerance",
        type=int,
        default=24,
        help="Color tolerance for checker background removal.",
    )
    parser.add_argument(
        "--no-remove-background",
        action="store_true",
        help="Disable checker background removal.",
    )
    parser.add_argument(
        "--no-fill-small-holes",
        action="store_true",
        help="Disable small hole filling in alpha cleanup.",
    )
    parser.add_argument(
        "--no-remove-isolated-pixels",
        action="store_true",
        help="Disable isolated-pixel removal in alpha cleanup.",
    )
    parser.add_argument(
        "--crop-padding",
        type=int,
        default=2,
        help="Base padding to keep around the detected subject bounds.",
    )
    parser.add_argument(
        "--crop-pad-top",
        type=int,
        default=None,
        help="Override the top crop padding.",
    )
    parser.add_argument(
        "--crop-pad-left",
        type=int,
        default=None,
        help="Override the left crop padding.",
    )
    parser.add_argument(
        "--crop-pad-right",
        type=int,
        default=None,
        help="Override the right crop padding.",
    )
    parser.add_argument(
        "--crop-pad-bottom",
        type=int,
        default=None,
        help="Override the bottom crop padding.",
    )
    parser.add_argument(
        "--sheet-columns",
        type=int,
        default=0,
        help="Number of columns in the sprite sheet grid. Use 0 for auto.",
    )
    parser.add_argument(
        "--sheet-padding",
        type=int,
        default=0,
        help="Padding in pixels between frames in the sprite sheet.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    options = CleanOptions(
        target_size=parse_size(args.size),
        colors=args.colors,
        scale=args.scale,
        cleanup=args.cleanup,
        fit=args.fit,
        alpha_threshold=args.alpha_threshold,
        outline_preserve=not args.no_outline_preserve,
        remove_background=not args.no_remove_background,
        background_tolerance=args.background_tolerance,
        fill_small_holes=not args.no_fill_small_holes,
        remove_isolated_pixels=not args.no_remove_isolated_pixels,
        crop_padding=(
            args.crop_pad_left if args.crop_pad_left is not None else args.crop_padding,
            args.crop_pad_top if args.crop_pad_top is not None else args.crop_padding,
            args.crop_pad_right if args.crop_pad_right is not None else args.crop_padding,
            args.crop_pad_bottom if args.crop_pad_bottom is not None else args.crop_padding,
        ),
        sheet_columns=args.sheet_columns,
        sheet_padding=args.sheet_padding,
    )
    result = clean_image(args.input, args.output_dir, options)
    report = result.report

    print(f"sheet: {result.fixed_path}")
    print(f"preview: {result.preview_path}")
    print(f"crop_preview: {result.crop_preview_path}")
    print(f"sheet_grid_preview: {result.sheet_grid_preview_path}")
    print(f"palette: {result.palette_path}")
    print(f"problem_map: {result.problem_map_path}")
    print(f"report: {result.report_path}")
    for frame_path in result.frame_paths:
        print(f"frame: {frame_path}")
    print(
        "summary: "
        f"{report['actual_palette_colors']} colors, "
        f"{report['isolated_pixels_before_cleanup']} isolated before cleanup, "
        f"{report['isolated_pixels_after_cleanup']} isolated after cleanup"
    )
    return 0
