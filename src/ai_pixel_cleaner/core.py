from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import json
import math

import numpy as np
from PIL import Image, ImageOps

FitMode = Literal["contain", "cover", "stretch"]
CleanupMode = Literal["off", "weak", "normal", "strong"]


@dataclass(frozen=True)
class CleanOptions:
    target_size: tuple[int, int] = (128, 128)
    colors: int = 32
    scale: int = 4
    cleanup: CleanupMode = "normal"
    fit: FitMode = "contain"
    alpha_threshold: int = 128
    outline_preserve: bool = True


@dataclass(frozen=True)
class CleanResult:
    output_dir: Path
    fixed_path: Path
    preview_path: Path
    palette_path: Path
    palette_hex_path: Path
    problem_map_path: Path
    report_path: Path
    report: dict


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
    options = options or CleanOptions()
    _validate_options(options)

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source = ImageOps.exif_transpose(Image.open(input_path)).convert("RGBA")
    fitted = _fit_to_canvas(source, options.target_size, options.fit)
    fitted_arr = np.asarray(fitted, dtype=np.uint8)

    alpha = fitted_arr[..., 3]
    visible_mask = alpha >= options.alpha_threshold
    semi_alpha_mask = (alpha > 0) & (alpha < 255)

    palette = _extract_palette(fitted_arr[..., :3], visible_mask, options.colors)
    quantized_rgba = _map_rgb_to_palette(fitted_arr[..., :3], visible_mask, palette)

    isolated_before = _detect_isolated_pixels(quantized_rgba)
    cleaned_rgba, changed_mask = _cleanup_pixels(
        quantized_rgba,
        options.cleanup,
        outline_preserve=options.outline_preserve,
    )
    isolated_after = _detect_isolated_pixels(cleaned_rgba)

    problem_mask = isolated_before | semi_alpha_mask | changed_mask
    final_indices = _indices_for_rgba(cleaned_rgba, palette)
    fixed = _make_paletted_image(final_indices, cleaned_rgba[..., 3] > 0, palette)

    fixed_path = output_dir / "fixed.png"
    preview_path = output_dir / f"preview_{options.scale}x.png"
    palette_path = output_dir / "palette.png"
    palette_hex_path = output_dir / "palette.hex"
    problem_map_path = output_dir / "problem_map.png"
    report_path = output_dir / "report.json"

    fixed.save(fixed_path)
    _make_preview(fixed, options.scale).save(preview_path)
    _make_palette_swatch(palette).save(palette_path)
    _write_palette_hex(palette_hex_path, palette)
    _make_problem_map(quantized_rgba, problem_mask, options.scale).save(problem_map_path)

    report = _build_report(
        input_path=input_path,
        source=source,
        options=options,
        palette=palette,
        visible_mask=visible_mask,
        semi_alpha_mask=semi_alpha_mask,
        isolated_before=isolated_before,
        isolated_after=isolated_after,
        changed_mask=changed_mask,
        fitted_rgb=fitted_arr[..., :3],
        quantized_rgba=quantized_rgba,
        output_paths={
            "fixed": fixed_path,
            "preview": preview_path,
            "palette": palette_path,
            "palette_hex": palette_hex_path,
            "problem_map": problem_map_path,
        },
    )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return CleanResult(
        output_dir=output_dir,
        fixed_path=fixed_path,
        preview_path=preview_path,
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


def _extract_palette(rgb: np.ndarray, visible_mask: np.ndarray, colors: int) -> np.ndarray:
    visible = rgb[visible_mask]
    if visible.size == 0:
        return np.array([[0, 0, 0]], dtype=np.uint8)

    unique, counts = np.unique(visible, axis=0, return_counts=True)
    if len(unique) <= colors:
        order = np.argsort(-counts)
        return unique[order].astype(np.uint8)

    strip = Image.fromarray(visible.reshape((1, len(visible), 3)))
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


def _map_rgb_to_palette(
    rgb: np.ndarray,
    visible_mask: np.ndarray,
    palette: np.ndarray,
) -> np.ndarray:
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


def _indices_for_rgba(rgba: np.ndarray, palette: np.ndarray) -> np.ndarray:
    height, width = rgba.shape[:2]
    indices = np.zeros((height, width), dtype=np.uint16)
    visible = rgba[..., 3] > 0
    if not np.any(visible):
        return indices
    indices[visible] = _nearest_palette_indices(rgba[visible, :3], palette)
    return indices


def _make_paletted_image(
    indices: np.ndarray,
    visible_mask: np.ndarray,
    palette: np.ndarray,
) -> Image.Image:
    height, width = visible_mask.shape
    has_transparency = not bool(np.all(visible_mask))
    data = np.zeros((height, width), dtype=np.uint8)

    palette_entries: list[int] = []
    if has_transparency:
        palette_entries.extend([0, 0, 0])
        data[visible_mask] = indices[visible_mask].astype(np.uint8) + 1
    else:
        data = indices.astype(np.uint8)

    for color in palette:
        palette_entries.extend(int(channel) for channel in color)
    palette_entries.extend([0] * (768 - len(palette_entries)))

    image = Image.new("P", (width, height))
    image.putdata(data.reshape(-1).tolist())
    image.putpalette(palette_entries)
    if has_transparency:
        image.info["transparency"] = 0
    return image


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


def _make_problem_map(rgba: np.ndarray, problem_mask: np.ndarray, scale: int) -> Image.Image:
    out = rgba.copy()
    if np.any(problem_mask):
        base = out[problem_mask, :3].astype(np.float32)
        red = np.array([255, 32, 32], dtype=np.float32)
        out[problem_mask, :3] = np.round(base * 0.25 + red * 0.75).astype(np.uint8)
        out[problem_mask, 3] = 255
    image = Image.fromarray(out)
    return image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)


def _build_report(
    input_path: Path,
    source: Image.Image,
    options: CleanOptions,
    palette: np.ndarray,
    visible_mask: np.ndarray,
    semi_alpha_mask: np.ndarray,
    isolated_before: np.ndarray,
    isolated_after: np.ndarray,
    changed_mask: np.ndarray,
    fitted_rgb: np.ndarray,
    quantized_rgba: np.ndarray,
    output_paths: dict[str, Path],
) -> dict:
    visible_pixels = int(np.count_nonzero(visible_mask))
    quant_error = _quantization_error(fitted_rgb, quantized_rgba, visible_mask)
    close_pairs = _count_close_palette_pairs(palette)
    return {
        "input": str(input_path),
        "source_size": list(source.size),
        "target_size": list(options.target_size),
        "fit": options.fit,
        "requested_colors": options.colors,
        "actual_palette_colors": int(len(palette)),
        "scale": options.scale,
        "cleanup": options.cleanup,
        "outline_preserve": options.outline_preserve,
        "visible_pixels": visible_pixels,
        "transparent_pixels": int(visible_mask.size - visible_pixels),
        "semi_transparent_pixels": int(np.count_nonzero(semi_alpha_mask)),
        "isolated_pixels_before_cleanup": int(np.count_nonzero(isolated_before)),
        "cleanup_changed_pixels": int(np.count_nonzero(changed_mask)),
        "isolated_pixels_after_cleanup": int(np.count_nonzero(isolated_after)),
        "close_palette_pairs": close_pairs,
        "quantization_error": quant_error,
        "outputs": {key: str(path) for key, path in output_paths.items()},
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
