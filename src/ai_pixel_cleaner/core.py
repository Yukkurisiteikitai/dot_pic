from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence
import json
import math

import numpy as np
from PIL import Image, ImageOps

FitMode = Literal["contain", "cover", "stretch"]
CleanupMode = Literal["off", "weak", "normal", "strong"]


@dataclass(frozen=True)
class CleanOptions:
    target_size: tuple[int, int] = (48, 48)
    colors: int = 16
    scale: int = 4
    cleanup: CleanupMode = "normal"
    fit: FitMode = "contain"
    alpha_threshold: int = 128
    outline_preserve: bool = False
    remove_background: bool = True
    background_tolerance: int = 24
    fill_small_holes: bool = True
    remove_isolated_pixels: bool = True
    crop_padding: int = 2
    sheet_columns: int = 0
    sheet_padding: int = 0


@dataclass(frozen=True)
class CleanResult:
    output_dir: Path
    frame_paths: list[Path]
    frame_preview_paths: list[Path]
    sheet_native_path: Path
    sheet_preview_path: Path
    palette_path: Path
    palette_hex_path: Path
    problem_map_path: Path
    report_path: Path
    report: dict

    @property
    def fixed_path(self) -> Path:
        return self.sheet_native_path

    @property
    def preview_path(self) -> Path:
        return self.sheet_preview_path


def parse_size(value: str) -> tuple[int, int]:
    text = value.lower().strip()
    if "x" in text:
        left, right = text.split("x", 1)
        width = int(left)
        height = int(right)
    else:
        width = int(text)
        height = width
    if width <= 0 or height <= 0:
        raise ValueError("size must be positive")
    if width > 512 or height > 512:
        raise ValueError("size must be 512px or smaller for this MVP")
    return width, height


def clean_image(
    input_path: str | Path,
    output_dir: str | Path,
    options: CleanOptions | None = None,
) -> CleanResult:
    return process_sprite_sheet([input_path], output_dir, options)


def process_sprite_sheet(
    input_paths: Sequence[str | Path],
    output_dir: str | Path,
    options: CleanOptions | None = None,
) -> CleanResult:
    options = options or CleanOptions()
    _validate_options(options)

    paths = [Path(path) for path in input_paths]
    if not paths:
        raise ValueError("at least one input image is required")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frame_dir = output_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    prepared_frames: list[dict] = []
    visible_pixels: list[np.ndarray] = []
    source_names: list[str] = []

    for index, path in enumerate(paths, start=1):
        source = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
        source_names.append(path.name)

        background_removed_pixels = 0
        if options.remove_background:
            source, background_removed_pixels = _remove_checker_background(
                source,
                options.background_tolerance,
            )

        cropped, crop_box = _crop_to_visible_bounds(source, options.crop_padding)
        fitted = _fit_to_canvas(cropped, options.target_size, options.fit)
        fitted_arr = np.asarray(fitted, dtype=np.uint8)

        soft_alpha = fitted_arr[..., 3]
        semi_alpha_mask = (soft_alpha > 0) & (soft_alpha < 255)
        binary_mask = soft_alpha >= options.alpha_threshold
        refined_mask = _refine_alpha_mask(binary_mask, options)
        alpha_changed_mask = binary_mask ^ refined_mask

        rgb = fitted_arr[..., :3].copy()
        rgb[~refined_mask] = 0
        prepared_frames.append(
            {
                "index": index,
                "path": path,
                "source_size": list(source.size),
                "background_removed_pixels": int(background_removed_pixels),
                "crop_box": list(crop_box),
                "fitted_rgb": rgb,
                "refined_mask": refined_mask,
                "semi_alpha_mask": semi_alpha_mask,
                "alpha_changed_mask": alpha_changed_mask,
            }
        )

        if np.any(refined_mask):
            visible_pixels.append(rgb[refined_mask])

    palette = _extract_palette(_stack_pixels(visible_pixels), options.colors)

    frame_images: list[Image.Image] = []
    frame_preview_paths: list[Path] = []
    frame_paths: list[Path] = []
    problem_frames: list[Image.Image] = []
    frame_reports: list[dict] = []

    for frame in prepared_frames:
        quantized = _map_rgb_to_palette(
            frame["fitted_rgb"],
            frame["refined_mask"],
            palette,
        )
        quantized[..., 3] = np.where(frame["refined_mask"], 255, 0).astype(np.uint8)
        quantized[~frame["refined_mask"], :3] = 0
        quant_error = _quantization_error(
            frame["fitted_rgb"],
            quantized,
            frame["refined_mask"],
        )

        isolated_before = _detect_isolated_pixels(quantized)
        cleaned_rgba, changed_mask = _cleanup_pixels(
            quantized,
            options.cleanup,
            outline_preserve=options.outline_preserve,
        )
        cleaned_rgba[..., 3] = np.where(frame["refined_mask"], 255, 0).astype(np.uint8)
        cleaned_rgba[~frame["refined_mask"], :3] = 0
        isolated_after = _detect_isolated_pixels(cleaned_rgba)

        frame_problem_mask = (
            frame["semi_alpha_mask"]
            | frame["alpha_changed_mask"]
            | isolated_before
            | changed_mask
        )

        frame_image = Image.fromarray(cleaned_rgba, "RGBA")
        frame_problem = _make_problem_map(frame_image, frame_problem_mask, 1)

        frame_path = frame_dir / f"frame_{frame['index']:03d}.png"
        frame_preview_path = frame_dir / f"frame_{frame['index']:03d}_{options.scale}x.png"
        frame_image.save(frame_path)
        _make_preview(frame_image, options.scale).save(frame_preview_path)

        frame_images.append(frame_image)
        problem_frames.append(frame_problem)
        frame_paths.append(frame_path)
        frame_preview_paths.append(frame_preview_path)
        frame_reports.append(
            {
                "input": str(frame["path"]),
                "source_name": frame["path"].name,
                "source_size": frame["source_size"],
                "background_removed_pixels": frame["background_removed_pixels"],
                "crop_box": frame["crop_box"],
                "target_size": list(options.target_size),
                "visible_pixels": int(np.count_nonzero(frame["refined_mask"])),
                "transparent_pixels": int(frame["refined_mask"].size - np.count_nonzero(frame["refined_mask"])),
                "semi_transparent_pixels": int(np.count_nonzero(frame["semi_alpha_mask"])),
                "alpha_changed_pixels": int(np.count_nonzero(frame["alpha_changed_mask"])),
                "isolated_pixels_before_cleanup": int(np.count_nonzero(isolated_before)),
                "cleanup_changed_pixels": int(np.count_nonzero(changed_mask)),
                "isolated_pixels_after_cleanup": int(np.count_nonzero(isolated_after)),
                "quantization_error": quant_error,
            }
        )

    columns = options.sheet_columns if options.sheet_columns > 0 else max(
        1,
        math.ceil(math.sqrt(len(frame_images))),
    )
    columns = min(columns, len(frame_images))
    sheet_native = _compose_sheet(frame_images, columns, options.sheet_padding)
    problem_sheet = _compose_sheet(problem_frames, columns, options.sheet_padding)
    sheet_preview = _make_preview(sheet_native, options.scale)
    problem_map_preview = _make_preview(problem_sheet, options.scale)

    native_sheet_path = output_dir / "sprite_sheet.png"
    sheet_preview_path = output_dir / f"sprite_sheet_{options.scale}x.png"
    palette_path = output_dir / "palette.png"
    palette_hex_path = output_dir / "palette.hex"
    problem_map_path = output_dir / "problem_map.png"
    report_path = output_dir / "report.json"

    sheet_native.save(native_sheet_path)
    sheet_preview.save(sheet_preview_path)
    _make_palette_swatch(palette).save(palette_path)
    _write_palette_hex(palette_hex_path, palette)
    problem_map_preview.save(problem_map_path)

    report = _build_report(
        input_paths=paths,
        options=options,
        palette=palette,
        frame_reports=frame_reports,
        frame_paths=frame_paths,
        frame_preview_paths=frame_preview_paths,
        sheet_native_path=native_sheet_path,
        sheet_preview_path=sheet_preview_path,
        palette_path=palette_path,
        palette_hex_path=palette_hex_path,
        problem_map_path=problem_map_path,
        columns=columns,
        rows=math.ceil(len(frame_images) / columns),
    )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return CleanResult(
        output_dir=output_dir,
        frame_paths=frame_paths,
        frame_preview_paths=frame_preview_paths,
        sheet_native_path=native_sheet_path,
        sheet_preview_path=sheet_preview_path,
        palette_path=palette_path,
        palette_hex_path=palette_hex_path,
        problem_map_path=problem_map_path,
        report_path=report_path,
        report=report,
    )


def _validate_options(options: CleanOptions) -> None:
    width, height = options.target_size
    if width <= 0 or height <= 0:
        raise ValueError("target size must be positive")
    if width > 512 or height > 512:
        raise ValueError("target size must be 512px or smaller for this MVP")
    if options.colors < 2 or options.colors > 255:
        raise ValueError("colors must be between 2 and 255")
    if options.scale < 1 or options.scale > 16:
        raise ValueError("scale must be between 1 and 16")
    if not 0 <= options.alpha_threshold <= 255:
        raise ValueError("alpha threshold must be between 0 and 255")
    if not 0 <= options.background_tolerance <= 255:
        raise ValueError("background tolerance must be between 0 and 255")
    if options.crop_padding < 0 or options.crop_padding > 128:
        raise ValueError("crop padding must be between 0 and 128")
    if options.sheet_columns < 0:
        raise ValueError("sheet columns must be 0 or greater")
    if options.sheet_padding < 0 or options.sheet_padding > 64:
        raise ValueError("sheet padding must be between 0 and 64")


def _remove_checker_background(image: Image.Image, tolerance: int) -> tuple[Image.Image, int]:
    arr = np.asarray(image, dtype=np.uint8).copy()
    alpha = arr[..., 3]
    rgb = arr[..., :3]
    candidates = _estimate_background_colors(rgb, alpha)
    if not candidates:
        return image, 0

    background_mask = _connected_background_mask(rgb, alpha, candidates, tolerance)
    removed = background_mask & (alpha > 0)
    if not np.any(removed):
        return image, 0

    arr[removed, :3] = 0
    arr[removed, 3] = 0
    return Image.fromarray(arr, "RGBA"), int(np.count_nonzero(removed))


def _estimate_background_colors(rgb: np.ndarray, alpha: np.ndarray) -> list[np.ndarray]:
    mask = alpha > 0
    height, width = alpha.shape
    border_pixels = []
    for x in range(width):
        if mask[0, x]:
            border_pixels.append(rgb[0, x])
        if height > 1 and mask[height - 1, x]:
            border_pixels.append(rgb[height - 1, x])
    for y in range(1, height - 1):
        if mask[y, 0]:
            border_pixels.append(rgb[y, 0])
        if width > 1 and mask[y, width - 1]:
            border_pixels.append(rgb[y, width - 1])

    if not border_pixels:
        return []

    border = np.asarray(border_pixels, dtype=np.uint8)
    unique, counts = np.unique(border, axis=0, return_counts=True)
    order = np.argsort(-counts)
    limit = min(2, len(unique))
    return [unique[index] for index in order[:limit]]


def _connected_background_mask(
    rgb: np.ndarray,
    alpha: np.ndarray,
    candidates: Sequence[np.ndarray],
    tolerance: int,
) -> np.ndarray:
    height, width = alpha.shape
    if height == 0 or width == 0:
        return np.zeros((height, width), dtype=bool)

    candidate_mask = np.zeros((height, width), dtype=bool)
    rgb_i = rgb.astype(np.int16)
    for candidate in candidates:
        delta = np.abs(rgb_i - candidate.astype(np.int16))
        candidate_mask |= (alpha > 0) & (np.max(delta, axis=2) <= tolerance)

    if not np.any(candidate_mask):
        return candidate_mask

    visited = np.zeros_like(candidate_mask)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(y: int, x: int) -> None:
        if candidate_mask[y, x] and not visited[y, x]:
            visited[y, x] = True
            queue.append((y, x))

    for x in range(width):
        enqueue(0, x)
        enqueue(height - 1, x)
    for y in range(height):
        enqueue(y, 0)
        enqueue(y, width - 1)

    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1))
    while queue:
        y, x = queue.popleft()
        for dy, dx in neighbors:
            ny = y + dy
            nx = x + dx
            if 0 <= ny < height and 0 <= nx < width and candidate_mask[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))

    return visited


def _crop_to_visible_bounds(image: Image.Image, padding: int) -> tuple[Image.Image, tuple[int, int, int, int]]:
    arr = np.asarray(image, dtype=np.uint8)
    visible = arr[..., 3] > 0
    if not np.any(visible):
        return image, (0, 0, image.width, image.height)

    ys, xs = np.where(visible)
    left = max(0, int(xs.min()) - padding)
    top = max(0, int(ys.min()) - padding)
    right = min(image.width, int(xs.max()) + 1 + padding)
    bottom = min(image.height, int(ys.max()) + 1 + padding)
    if left >= right or top >= bottom:
        return image, (0, 0, image.width, image.height)
    return image.crop((left, top, right, bottom)), (left, top, right, bottom)


def _fit_to_canvas(image: Image.Image, target_size: tuple[int, int], fit: FitMode) -> Image.Image:
    target_width, target_height = target_size
    if fit == "stretch":
        return image.resize(target_size, Image.Resampling.LANCZOS)

    source_width, source_height = image.size
    if fit == "contain":
        ratio = min(target_width / source_width, target_height / source_height)
        new_size = (
            max(1, round(source_width * ratio)),
            max(1, round(source_height * ratio)),
        )
        resized = image.resize(new_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
        x = (target_width - resized.width) // 2
        y = (target_height - resized.height) // 2
        canvas.alpha_composite(resized, (x, y))
        return canvas

    if fit == "cover":
        ratio = max(target_width / source_width, target_height / source_height)
        new_size = (
            max(1, round(source_width * ratio)),
            max(1, round(source_height * ratio)),
        )
        resized = image.resize(new_size, Image.Resampling.LANCZOS)
        left = max(0, (resized.width - target_width) // 2)
        top = max(0, (resized.height - target_height) // 2)
        return resized.crop((left, top, left + target_width, top + target_height))

    raise ValueError(f"unsupported fit mode: {fit}")


def _refine_alpha_mask(mask: np.ndarray, options: CleanOptions) -> np.ndarray:
    refined = mask.copy()
    if options.cleanup == "off":
        return refined

    iterations = {
        "weak": 1,
        "normal": 1,
        "strong": 2,
    }[options.cleanup]
    fill_threshold = {
        "weak": 5,
        "normal": 5,
        "strong": 4,
    }[options.cleanup]
    keep_threshold = {
        "weak": 1,
        "normal": 2,
        "strong": 2,
    }[options.cleanup]

    for _ in range(iterations):
        neighbors = _neighbor_count(refined)
        if options.fill_small_holes:
            refined = refined | (~refined & (neighbors >= fill_threshold))
        neighbors = _neighbor_count(refined)
        if options.remove_isolated_pixels:
            refined = refined & (neighbors >= keep_threshold)

    return refined


def _neighbor_count(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask.astype(np.uint8), 1, constant_values=0)
    height, width = mask.shape
    count = np.zeros((height, width), dtype=np.uint8)
    for dy in range(3):
        for dx in range(3):
            if dy == 1 and dx == 1:
                continue
            count += padded[dy : dy + height, dx : dx + width]
    return count


def _extract_palette(pixels: np.ndarray, colors: int) -> np.ndarray:
    if pixels.size == 0:
        return np.array([[0, 0, 0]], dtype=np.uint8)

    unique, counts = np.unique(pixels, axis=0, return_counts=True)
    if len(unique) <= colors:
        order = np.argsort(-counts)
        return unique[order].astype(np.uint8)

    strip = Image.fromarray(pixels.reshape((1, len(pixels), 3)))
    quantized = strip.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    palette_raw = quantized.getpalette()
    used_indices, used_counts = np.unique(np.asarray(quantized), return_counts=True)
    order = np.argsort(-used_counts)

    palette = []
    for index in used_indices[order]:
        offset = int(index) * 3
        palette.append(palette_raw[offset : offset + 3])
    return np.asarray(palette, dtype=np.uint8)


def _stack_pixels(pixels: Sequence[np.ndarray]) -> np.ndarray:
    non_empty = [array for array in pixels if array.size > 0]
    if not non_empty:
        return np.empty((0, 3), dtype=np.uint8)
    return np.concatenate(non_empty, axis=0)


def _map_rgb_to_palette(rgb: np.ndarray, visible_mask: np.ndarray, palette: np.ndarray) -> np.ndarray:
    height, width = visible_mask.shape
    out = np.zeros((height, width, 4), dtype=np.uint8)
    if not np.any(visible_mask):
        return out

    visible_rgb = rgb[visible_mask]
    indices = _nearest_palette_indices(visible_rgb, palette)
    out[visible_mask, :3] = palette[indices]
    out[visible_mask, 3] = 255
    return out


def _nearest_palette_indices(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    rgb_i = rgb.astype(np.int32)
    palette_i = palette.astype(np.int32)
    result = np.empty((len(rgb_i),), dtype=np.uint16)
    chunk_size = 8192
    for start in range(0, len(rgb_i), chunk_size):
        chunk = rgb_i[start : start + chunk_size]
        delta = chunk[:, None, :] - palette_i[None, :, :]
        distance = np.sum(delta * delta, axis=2)
        result[start : start + chunk_size] = np.argmin(distance, axis=1)
    return result


def _detect_isolated_pixels(rgba: np.ndarray) -> np.ndarray:
    height, width = rgba.shape[:2]
    opaque = rgba[..., 3] > 0
    rgb = rgba[..., :3]
    isolated = np.zeros((height, width), dtype=bool)

    for y in range(height):
        y0 = max(0, y - 1)
        y1 = min(height, y + 2)
        for x in range(width):
            if not opaque[y, x]:
                continue
            x0 = max(0, x - 1)
            x1 = min(width, x + 2)
            local_opaque = opaque[y0:y1, x0:x1]
            local_rgb = rgb[y0:y1, x0:x1]
            same = np.all(local_rgb == rgb[y, x], axis=2) & local_opaque
            same_count = int(np.count_nonzero(same)) - 1
            if same_count == 0:
                isolated[y, x] = True
    return isolated


def _cleanup_pixels(
    rgba: np.ndarray,
    cleanup: CleanupMode,
    outline_preserve: bool,
) -> tuple[np.ndarray, np.ndarray]:
    settings = {
        "off": None,
        "weak": (0, 5),
        "normal": (1, 5),
        "strong": (2, 4),
    }
    setting = settings[cleanup]
    if setting is None:
        return rgba.copy(), np.zeros(rgba.shape[:2], dtype=bool)

    max_same_neighbors, min_dominant_neighbors = setting
    source = rgba.copy()
    out = rgba.copy()
    changed = np.zeros(rgba.shape[:2], dtype=bool)
    height, width = rgba.shape[:2]
    opaque = source[..., 3] > 0
    rgb = source[..., :3]

    for y in range(height):
        y0 = max(0, y - 1)
        y1 = min(height, y + 2)
        for x in range(width):
            if not opaque[y, x]:
                continue
            x0 = max(0, x - 1)
            x1 = min(width, x + 2)
            local_opaque = opaque[y0:y1, x0:x1].copy()
            local_rgb = rgb[y0:y1, x0:x1]
            center_y = y - y0
            center_x = x - x0
            local_opaque[center_y, center_x] = False

            if outline_preserve and not np.all(opaque[y0:y1, x0:x1]):
                continue

            neighbor_colors = local_rgb[local_opaque]
            if len(neighbor_colors) == 0:
                continue

            same_count = int(np.count_nonzero(np.all(neighbor_colors == rgb[y, x], axis=1)))
            if same_count > max_same_neighbors:
                continue

            unique, counts = np.unique(neighbor_colors, axis=0, return_counts=True)
            best = int(np.argmax(counts))
            if int(counts[best]) < min_dominant_neighbors:
                continue
            replacement = unique[best]
            if np.array_equal(replacement, rgb[y, x]):
                continue

            out[y, x, :3] = replacement
            changed[y, x] = True

    return out, changed


def _compose_sheet(images: Sequence[Image.Image], columns: int, padding: int) -> Image.Image:
    if not images:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    frame_width, frame_height = images[0].size
    rows = math.ceil(len(images) / columns)
    sheet_width = columns * frame_width + max(0, columns - 1) * padding
    sheet_height = rows * frame_height + max(0, rows - 1) * padding
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))

    for index, image in enumerate(images):
        row = index // columns
        column = index % columns
        x = column * (frame_width + padding)
        y = row * (frame_height + padding)
        sheet.alpha_composite(image, (x, y))

    return sheet


def _make_preview(image: Image.Image, scale: int) -> Image.Image:
    width, height = image.size
    return image.convert("RGBA").resize(
        (width * scale, height * scale),
        Image.Resampling.NEAREST,
    )


def _make_palette_swatch(palette: np.ndarray, swatch_size: int = 16) -> Image.Image:
    columns = min(16, max(1, len(palette)))
    rows = max(1, math.ceil(len(palette) / columns))
    image = Image.new("RGB", (columns * swatch_size, rows * swatch_size), (0, 0, 0))
    for index, color in enumerate(palette):
        x = (index % columns) * swatch_size
        y = (index // columns) * swatch_size
        block = Image.new("RGB", (swatch_size, swatch_size), tuple(int(c) for c in color))
        image.paste(block, (x, y))
    return image


def _write_palette_hex(path: Path, palette: np.ndarray) -> None:
    lines = ["#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b)) for r, g, b in palette]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_problem_map(rgba: np.ndarray | Image.Image, problem_mask: np.ndarray, scale: int) -> Image.Image:
    if isinstance(rgba, Image.Image):
        out = np.asarray(rgba.convert("RGBA"), dtype=np.uint8).copy()
    else:
        out = rgba.copy()
    if np.any(problem_mask):
        base = out[problem_mask, :3].astype(np.float32)
        red = np.array([255, 32, 32], dtype=np.float32)
        out[problem_mask, :3] = np.round(base * 0.25 + red * 0.75).astype(np.uint8)
        out[problem_mask, 3] = 255
    image = Image.fromarray(out, "RGBA")
    if scale == 1:
        return image
    return image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)


def _build_report(
    input_paths: Sequence[Path],
    options: CleanOptions,
    palette: np.ndarray,
    frame_reports: Sequence[dict],
    frame_paths: Sequence[Path],
    frame_preview_paths: Sequence[Path],
    sheet_native_path: Path,
    sheet_preview_path: Path,
    palette_path: Path,
    palette_hex_path: Path,
    problem_map_path: Path,
    columns: int,
    rows: int,
) -> dict:
    visible_pixels = sum(frame["visible_pixels"] for frame in frame_reports)
    semi_transparent_pixels = sum(frame["semi_transparent_pixels"] for frame in frame_reports)
    isolated_before = sum(frame["isolated_pixels_before_cleanup"] for frame in frame_reports)
    isolated_after = sum(frame["isolated_pixels_after_cleanup"] for frame in frame_reports)
    changed_pixels = sum(frame["cleanup_changed_pixels"] for frame in frame_reports)

    return {
        "inputs": [str(path) for path in input_paths],
        "frame_count": len(frame_reports),
        "source_names": [path.name for path in input_paths],
        "target_size": list(options.target_size),
        "fit": options.fit,
        "requested_colors": options.colors,
        "actual_palette_colors": int(len(palette)),
        "scale": options.scale,
        "cleanup": options.cleanup,
        "outline_preserve": options.outline_preserve,
        "remove_background": options.remove_background,
        "background_tolerance": options.background_tolerance,
        "crop_padding": options.crop_padding,
        "sheet_columns": columns,
        "sheet_rows": rows,
        "sheet_padding": options.sheet_padding,
        "visible_pixels": visible_pixels,
        "transparent_pixels": int(len(frame_reports) * options.target_size[0] * options.target_size[1] - visible_pixels),
        "semi_transparent_pixels": semi_transparent_pixels,
        "isolated_pixels_before_cleanup": isolated_before,
        "cleanup_changed_pixels": changed_pixels,
        "isolated_pixels_after_cleanup": isolated_after,
        "close_palette_pairs": _count_close_palette_pairs(palette),
        "quantization_error": _quantization_error_from_frames(frame_reports),
        "outputs": {
            "frame_paths": [str(path) for path in frame_paths],
            "frame_preview_paths": [str(path) for path in frame_preview_paths],
            "sheet_native": str(sheet_native_path),
            "sheet_preview": str(sheet_preview_path),
            "palette": str(palette_path),
            "palette_hex": str(palette_hex_path),
            "problem_map": str(problem_map_path),
        },
        "frames": list(frame_reports),
    }


def _quantization_error_from_frames(frame_reports: Sequence[dict]) -> dict:
    if not frame_reports:
        return {"mean": 0.0, "max": 0.0, "pixels_over_30": 0}
    means = np.array([frame["quantization_error"]["mean"] for frame in frame_reports], dtype=np.float32)
    max_values = np.array([frame["quantization_error"]["max"] for frame in frame_reports], dtype=np.float32)
    pixels_over_30 = sum(frame["quantization_error"]["pixels_over_30"] for frame in frame_reports)
    return {
        "mean": round(float(np.mean(means)), 2),
        "max": round(float(np.max(max_values)), 2),
        "pixels_over_30": int(pixels_over_30),
    }


def _quantization_error(
    fitted_rgb: np.ndarray,
    quantized_rgba: np.ndarray,
    visible_mask: np.ndarray,
) -> dict:
    if not np.any(visible_mask):
        return {"mean": 0.0, "max": 0.0, "pixels_over_30": 0}
    source = fitted_rgb[visible_mask].astype(np.int32)
    quantized = quantized_rgba[visible_mask, :3].astype(np.int32)
    delta = source - quantized
    distance = np.sqrt(np.sum(delta * delta, axis=1))
    return {
        "mean": round(float(np.mean(distance)), 2),
        "max": round(float(np.max(distance)), 2),
        "pixels_over_30": int(np.count_nonzero(distance > 30)),
    }


def _count_close_palette_pairs(palette: np.ndarray, threshold: float = 14.0) -> int:
    if len(palette) < 2:
        return 0
    palette_i = palette.astype(np.int32)
    count = 0
    for index in range(len(palette_i)):
        delta = palette_i[index + 1 :] - palette_i[index]
        if len(delta) == 0:
            continue
        distance = np.sqrt(np.sum(delta * delta, axis=1))
        count += int(np.count_nonzero(distance < threshold))
    return count
