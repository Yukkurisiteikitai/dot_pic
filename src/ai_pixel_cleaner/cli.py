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
        help="Directory for fixed.png, preview, palette, problem map, and report.",
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
    )
    result = clean_image(args.input, args.output_dir, options)
    report = result.report

    print(f"fixed: {result.fixed_path}")
    print(f"preview: {result.preview_path}")
    print(f"palette: {result.palette_path}")
    print(f"problem_map: {result.problem_map_path}")
    print(f"report: {result.report_path}")
    print(
        "summary: "
        f"{report['actual_palette_colors']} colors, "
        f"{report['isolated_pixels_before_cleanup']} isolated before cleanup, "
        f"{report['isolated_pixels_after_cleanup']} isolated after cleanup"
    )
    return 0
