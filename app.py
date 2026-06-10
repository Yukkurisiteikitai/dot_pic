from __future__ import annotations

from pathlib import Path
import tempfile

import streamlit as st

from ai_pixel_cleaner import CleanOptions, clean_image


st.set_page_config(page_title="AI Pixel Cleaner", layout="wide")
st.title("AI Pixel Cleaner")

with st.sidebar:
    target = st.selectbox("target size", [64, 96, 128, 256], index=2)
    colors = st.select_slider("colors", options=[16, 24, 32, 48, 64], value=32)
    cleanup = st.select_slider("noise cleanup", options=["off", "weak", "normal", "strong"], value="normal")
    fit = st.segmented_control("fit", options=["contain", "cover", "stretch"], default="contain")
    outline_preserve = st.toggle("outline preserve", value=True)
    scale = st.slider("preview scale", min_value=2, max_value=8, value=4)

uploaded = st.file_uploader("input", type=["png", "jpg", "jpeg", "webp"])

if uploaded is not None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        input_path = root / uploaded.name
        output_dir = root / "outputs"
        input_path.write_bytes(uploaded.getbuffer())

        options = CleanOptions(
            target_size=(target, target),
            colors=int(colors),
            scale=int(scale),
            cleanup=cleanup,
            fit=fit or "contain",
            outline_preserve=outline_preserve,
        )
        result = clean_image(input_path, output_dir, options)

        left, center, right = st.columns(3)
        with left:
            st.image(str(input_path), caption="input", use_container_width=True)
        with center:
            st.image(str(result.preview_path), caption="fixed preview", use_container_width=True)
        with right:
            st.image(str(result.problem_map_path), caption="problem map", use_container_width=True)

        metric_cols = st.columns(4)
        metric_cols[0].metric("palette", result.report["actual_palette_colors"])
        metric_cols[1].metric("isolated before", result.report["isolated_pixels_before_cleanup"])
        metric_cols[2].metric("isolated after", result.report["isolated_pixels_after_cleanup"])
        metric_cols[3].metric("changed", result.report["cleanup_changed_pixels"])

        downloads = st.columns(5)
        for column, label, path, mime in [
            (downloads[0], "fixed.png", result.fixed_path, "image/png"),
            (downloads[1], "preview.png", result.preview_path, "image/png"),
            (downloads[2], "palette.png", result.palette_path, "image/png"),
            (downloads[3], "problem_map.png", result.problem_map_path, "image/png"),
            (downloads[4], "report.json", result.report_path, "application/json"),
        ]:
            column.download_button(
                label,
                data=path.read_bytes(),
                file_name=path.name,
                mime=mime,
            )
