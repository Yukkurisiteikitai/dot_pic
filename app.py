from __future__ import annotations

from pathlib import Path
import tempfile
import sys

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from ai_pixel_cleaner import CleanOptions, process_sprite_sheet


def main() -> None:
    st.set_page_config(page_title="AI Pixel Cleaner", layout="wide")
    st.title("AI Pixel Cleaner")

    with st.sidebar:
        st.header("Input")
        uploaded_files = st.file_uploader(
            "Upload one or more images",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
        )

        st.header("Pixel Settings")
        target = st.selectbox("target size", [32, 48, 64, 96, 128], index=2)
        colors = st.select_slider("palette colors", options=[8, 12, 16, 24, 32], value=16)
        scale = st.select_slider("preview scale", options=[2, 4, 6, 8], value=4)
        cleanup = st.selectbox("cleanup", ["off", "weak", "normal", "strong"], index=2)
        fit = st.radio("fit", options=["contain", "cover", "stretch"], horizontal=True, index=0)
        alpha_threshold = st.slider("alpha threshold", min_value=0, max_value=255, value=128)

        st.header("Background")
        remove_background = st.toggle("remove checker background", value=True)
        background_tolerance = st.slider("background tolerance", min_value=0, max_value=255, value=24)
        crop_padding = st.slider("crop padding", min_value=0, max_value=16, value=2)

        st.header("Cleanup")
        fill_small_holes = st.toggle("fill small holes", value=True)
        remove_isolated_pixels = st.toggle("remove isolated pixels", value=True)
        outline_preserve = st.toggle("preserve outline", value=False)

        st.header("Sprite Sheet")
        sheet_columns = st.number_input("columns", min_value=0, max_value=32, value=0, step=1)
        sheet_padding = st.slider("padding", min_value=0, max_value=16, value=0)

        process_button = st.button("Process")

    if not uploaded_files:
        st.info("Upload one or more images to generate a transparent sprite sheet.")
        return

    workdir = Path(st.session_state.setdefault("workdir", tempfile.mkdtemp(prefix="ai_pixel_cleaner_")))
    input_paths: list[Path] = []
    upload_signature: list[tuple[str, int]] = []
    for index, uploaded in enumerate(uploaded_files, start=1):
        path = workdir / f"{index:03d}_{uploaded.name}"
        path.write_bytes(uploaded.getbuffer())
        input_paths.append(path)
        upload_signature.append((uploaded.name, hash(uploaded.getvalue())))

    options = CleanOptions(
        target_size=(int(target), int(target)),
        colors=int(colors),
        scale=int(scale),
        cleanup=cleanup,
        fit=fit,
        alpha_threshold=int(alpha_threshold),
        outline_preserve=outline_preserve,
        remove_background=remove_background,
        background_tolerance=int(background_tolerance),
        fill_small_holes=fill_small_holes,
        remove_isolated_pixels=remove_isolated_pixels,
        crop_padding=int(crop_padding),
        sheet_columns=int(sheet_columns),
        sheet_padding=int(sheet_padding),
    )

    signature = (
        tuple(upload_signature),
        options.target_size,
        options.colors,
        options.scale,
        options.cleanup,
        options.fit,
        options.alpha_threshold,
        options.outline_preserve,
        options.remove_background,
        options.background_tolerance,
        options.fill_small_holes,
        options.remove_isolated_pixels,
        options.crop_padding,
        options.sheet_columns,
        options.sheet_padding,
    )

    needs_process = process_button or st.session_state.get("last_signature") != signature or "last_result" not in st.session_state
    if needs_process:
        if not input_paths:
            st.warning("No input files were provided.")
            return
        with st.spinner("Processing..."):
            result = process_sprite_sheet(input_paths, workdir / "outputs", options)
            st.session_state["last_result"] = result
            st.session_state["last_signature"] = signature
    else:
        result = st.session_state["last_result"]

    st.subheader("Source")
    source_cols = st.columns(min(4, max(1, len(result.report["inputs"]))))
    for index, frame in enumerate(result.report["frames"]):
        column = source_cols[index % len(source_cols)]
        with column:
            st.image(frame["input"], caption=frame["source_name"], use_container_width=True)

    metric_cols = st.columns(4)
    metric_cols[0].metric("palette", result.report["actual_palette_colors"])
    metric_cols[1].metric("frames", result.report["frame_count"])
    metric_cols[2].metric("isolated before", result.report["isolated_pixels_before_cleanup"])
    metric_cols[3].metric("isolated after", result.report["isolated_pixels_after_cleanup"])

    sheet_col, preview_col = st.columns(2)
    with sheet_col:
        st.image(str(result.fixed_path), caption="sprite sheet", use_container_width=True)
    with preview_col:
        st.image(str(result.preview_path), caption="preview", use_container_width=True)

    aux_col, palette_col = st.columns(2)
    with aux_col:
        st.image(str(result.problem_map_path), caption="problem map", use_container_width=True)
    with palette_col:
        st.image(str(result.palette_path), caption="palette", use_container_width=True)

    if result.frame_preview_paths:
        st.subheader("Frames")
        frame_cols = st.columns(min(4, max(1, len(result.frame_preview_paths))))
        for index, path in enumerate(result.frame_preview_paths):
            with frame_cols[index % len(frame_cols)]:
                st.image(str(path), caption=path.name, use_container_width=True)

    st.subheader("Downloads")
    download_cols = st.columns(6)
    download_items = [
        ("sprite_sheet.png", result.fixed_path, "image/png"),
        (result.preview_path.name, result.preview_path, "image/png"),
        (result.problem_map_path.name, result.problem_map_path, "image/png"),
        (result.palette_path.name, result.palette_path, "image/png"),
        (result.palette_hex_path.name, result.palette_hex_path, "text/plain"),
        (result.report_path.name, result.report_path, "application/json"),
    ]
    for column, (label, path, mime) in zip(download_cols, download_items, strict=False):
        with column:
            st.download_button(label, data=path.read_bytes(), file_name=path.name, mime=mime)

    st.subheader("Report")
    st.json(result.report)


if __name__ == "__main__":
    if get_script_run_ctx() is None:
        print("Run this app with: uv run streamlit run app.py", file=sys.stderr)
        raise SystemExit(0)
    main()
