"""image -> ocr -> style -> group -> shapes -> roles -> inpaint -> cutout -> layers -> document.json"""
import logging
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import config
from .engines import lama, llm, vision_ocr
from .schemas import Document
from .stages import compose, elements, grouping, photos, roles, segment, shapes, text_style

log = logging.getLogger(__name__)


class Options(BaseModel):
    cutout_subject: bool = True
    use_llm: bool = config.USE_LLM
    scale: float = Field(1.0, ge=1, le=4, description="2 for retina screenshots: halves every size")
    sans_font: str = config.SANS_FONT
    serif_font: str = config.SERIF_FONT
    script_font: str = config.SCRIPT_FONT


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def _load(path: Path) -> np.ndarray:
    im = ImageOps.exif_transpose(Image.open(path))
    if im.mode in ("RGBA", "LA", "P"):  # flatten transparency onto white
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    if max(im.size) > config.MAX_IMAGE_SIDE:
        im.thumbnail((config.MAX_IMAGE_SIDE, config.MAX_IMAGE_SIDE), Image.LANCZOS)
    return np.array(im)


def _print_on_subject(img: np.ndarray, rgba: np.ndarray, box, editable: list, blocks: list) -> int:
    """Text lying on the opaque cut-out and stacked with packaging print already kept in the picture
    ("Shampoo" under "Bond Retain Hydrating") is print on the product: it has to move with it. Put its original pixels back on the cut-out and drop it from the text layers. Other text over
    the subject (a headline or disclaimer across a model) stays editable."""
    if not editable:
        return 0

    def on_cutout(b) -> tuple[int, int, int, int] | None:
        x0, y0 = b.box.x - box.x, b.box.y - box.y
        x1, y1 = x0 + b.box.w, y0 + b.box.h
        if x0 < 0 or y0 < 0 or x1 > rgba.shape[1] or y1 > rgba.shape[0]:
            return None
        return (x0, y0, x1, y1) if (rgba[y0:y1, x0:x1, 3] > 200).mean() >= 0.8 else None

    def stacked(b, k) -> bool:  # same column of a pack, right above or below
        reach = b.font_size * 1.5
        return (min(k.box.x2, b.box.x2) > max(k.box.x, b.box.x)
                and abs(k.box.cy - b.box.cy) < (k.box.h + b.box.h) / 2 + reach)

    n, changed = 0, True
    while changed:  # a label under a label under packaging print
        changed = False
        for b in editable:
            if b.keep_in_background:
                continue
            spot = on_cutout(b)
            if spot is None:
                continue
            printed = [k for k in blocks if k.keep_in_background and k is not b]
            if not any(stacked(b, k) for k in printed):
                continue
            x0, y0, x1, y1 = spot
            pad = max(2, int(b.font_size * 0.4))
            px0, py0 = max(0, x0 - pad), max(0, y0 - pad)
            px1, py1 = min(rgba.shape[1], x1 + pad), min(rgba.shape[0], y1 + pad)
            solid = rgba[py0:py1, px0:px1, 3] > 0
            region = rgba[py0:py1, px0:px1, :3]
            region[solid] = img[box.y + py0:box.y + py1, box.x + px0:box.x + px1][solid]
            b.keep_in_background = changed = True
            n += 1
    return n


def run(image_path: Path, options: Options | None = None, job_id: str | None = None, source_name: str = "",
        progress: Callable[[str], None] = print) -> Document:
    opts = options or Options()
    job_id = job_id or new_job_id()
    out = config.OUTPUT_DIR / job_id
    out.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    warnings: list[str] = []
    t = time.perf_counter()

    def step(name: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[name] = round(now - t, 2)
        t = now

    img = _load(image_path)
    H, W = img.shape[:2]
    original = out / "original.png"
    Image.fromarray(img).save(original)
    progress(f"Loaded {W}×{H}")

    lines = vision_ocr.recognize(original, W, H)
    step("ocr")
    progress(f"Read {len(lines)} text line(s)")

    styled = []
    for i, line in enumerate(lines):
        s, _ = text_style.style_line(img, line)
        s.index = i
        styled.append(s)
    # Interfaces are rendered in flat colour (62-98% of pixels equal their neighbour); ads and photos
    # are not (11-38%). On a UI, uncertain OCR is a username or code (keep editable) and the "main
    # subject" is just some card or avatar (don't cut it out); in an ad it is packaging print.
    flat = float((np.abs(img[:, 1:].astype(np.int16) - img[:, :-1]).sum(axis=2) == 0).mean())
    flat_ui = flat >= config.UI_FLAT_SHARE
    blocks = grouping.group(styled, W, H, is_ui=flat_ui)
    if flat_ui:  # text printed on product shots / thumbnails belongs to those pictures
        n_photo = photos.keep_photo_text(img, blocks)
        if n_photo:
            progress(f"Kept {n_photo} text block(s) inside photos as part of the picture")
    # On a UI, cards / buttons / photos / icons are found by the elements stage instead
    found = [] if flat_ui else shapes.find(img, blocks)
    if not flat_ui:  # tinted label bands behind text on photos ("Smoothing" on a navy tag)
        found += shapes.find_tags(img, blocks, found)
    step("style")
    progress(f"Grouped into {len(blocks)} block(s), found {len(found)} button/shape(s)")

    overlay = out / "overlay.png"
    compose.draw_overlay(img, blocks, found).save(overlay)
    # The role labels (headline, CTA…) are written for ads; on a UI they mislead, and layers are
    # better named by their text, so the LLM step is skipped there.
    use_llm = opts.use_llm and not flat_ui
    warn = roles.label(blocks, found, overlay, use_llm)
    if warn:
        warnings.append(warn)
        progress(warn)
    if use_llm:
        llm.unload()
    # Shapes whose only text turned out to be part of the photo are not shapes either
    by_id = {b.id: b for b in blocks}
    found = [s for s in found if any(not by_id[i].keep_in_background for i in s.block_ids)]
    step("roles")
    editable = [b for b in blocks if not b.keep_in_background]
    progress(f"Labelled roles: {', '.join(f'{b.id}={b.role}' for b in editable) or 'none'}")

    fonts = {"sans": opts.sans_font, "serif": opts.serif_font, "script": opts.script_font}
    ui: elements.Decomposition | None = None
    if flat_ui:  # lift cards, buttons, photos, icons and text; the background keeps only the page
        text_mask = np.zeros((H, W), np.uint8)
        for b in editable:
            for line in b.lines:
                text_style.paint_erase_mask(text_mask, img, line)
        ui = elements.decompose(img, blocks, text_mask, out, fonts, opts.scale, by_text=True)
        clean, mask = ui.background, ui.mask
        c = ui.counts
        progress(f"Separated {c['cards']} card(s), {c['buttons']} button(s), {c['photos']} image(s), "
                 f"{c['icons']} icon/graphic(s)")
        step("elements")
    else:  # erase editable text + buttons from the picture
        fills, mask = shapes.plan_erase(img, found)
        for b in editable:
            for line in b.lines:
                text_style.paint_erase_mask(mask, img, line)
        clean, engine = lama.inpaint(img, mask)
        for shape_mask, colour in fills:
            clean[shape_mask > 0] = colour
            mask |= shape_mask
        step("inpaint_text")
        progress(f"Removed text from the background ({engine})")
        if engine.endswith("+opencv"):
            warnings.append("LaMa unavailable: background cleaned with basic OpenCV inpainting.")

    subject_box = None
    looks_like_ui = flat_ui or len(editable) >= config.UI_MIN_TEXT_BLOCKS
    photo_layers: list = []
    if not flat_ui:  # ads with flat areas: photo panels and product shots become image layers
        clean, photo_layers, photo_area = elements.lift_photos(img, clean, out, opts.scale)
        if photo_layers:
            progress(f"Separated {len(photo_layers)} photo / product image(s)")
        # things placed on a photo backdrop (product cards, stickers): their own layers, backdrop intact
        # (never inside a photo that is already its own layer: those are labels on its products)
        skip = mask | (photos.tile_mask(img) * 255).astype(np.uint8) if photo_layers else mask
        clean, object_layers, object_mask = elements.lift_objects(img, clean, out, opts.scale, skip=skip,
                                                                  avoid=[sh.box for sh in found])
        if object_layers:
            photo_layers += object_layers
            mask |= object_mask
            progress(f"Separated {len(object_layers)} placed object(s) (cards, products, stickers)")
            step("objects")
    else:
        photo_area = 0.0
    if opts.cutout_subject and looks_like_ui:
        progress("Looks like a UI screen: images kept in place, no subject cut-out")
    elif opts.cutout_subject and photo_area > 0.3:
        progress("The pictures are separate image layers already: no subject cut-out")
    elif opts.cutout_subject:
        try:
            cut = segment.cutout(clean)
        except Exception as e:
            log.warning("Subject cut-out failed: %s", e)
            warnings.append(f"Subject cut-out failed ({e.__class__.__name__}).")
            cut = None
        if cut is not None:
            rgba, subject_box = cut
            n_print = _print_on_subject(img, rgba, subject_box, editable, blocks)
            if n_print:
                progress(f"Kept {n_print} label(s) printed on the product with the cut-out")
            Image.fromarray(rgba, "RGBA").save(out / "subject.png")
            clean, _ = lama.inpaint(clean, segment.subject_mask(rgba, subject_box, H, W, img))
            progress("Cut out the main subject onto its own layer")
        else:
            progress("No separate subject to cut out (the photo stays as the background)")
        step("cutout")

    Image.fromarray(clean).save(out / "background.png")
    Image.fromarray(mask).save(out / "mask.png")
    compose.draw_overlay(img, blocks, found, subject_box).save(overlay)

    layers = compose.build_layers(blocks, found, fonts, W, H, subject_box, opts.scale, name_by_text=flat_ui,
                                  elements=ui.layers if ui else photo_layers,
                                  skip_blocks=ui.button_blocks if ui else None)
    doc = Document(
        job_id=job_id, source_name=source_name or image_path.name, width=round(W / opts.scale),
        height=round(H / opts.scale), layers=layers, fonts=fonts, timings=timings, warnings=warnings,
        files={k: f"{job_id}/{v}" for k, v in {"original": "original.png", "background": "background.png",
                                                "overlay": "overlay.png", "mask": "mask.png"}.items()},
    )
    for layer in doc.layers:
        if layer.src:
            layer.src = f"{job_id}/{layer.src}"
        if layer.icon_src:
            layer.icon_src = f"{job_id}/{layer.icon_src}"
    (out / "document.json").write_text(doc.model_dump_json(indent=2))
    (out / "blocks.json").write_text("[" + ",\n".join(b.model_dump_json() for b in blocks) + "]")
    progress(f"Done: {len(doc.layers)} layers in {sum(timings.values()):.1f}s")
    return doc
