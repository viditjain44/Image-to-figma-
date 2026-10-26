"""Turn blocks + shapes into the Figma layer list, and draw the debug overlay."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..schemas import FontKind, Layer, Shape, StyleRange, TextBlock
from .text_style import color_distance

WEIGHT_NAMES = {300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold",
                900: "Black"}
ROLE_NAMES = {"eyebrow": "Eyebrow", "headline": "Headline", "subtext": "Subtext", "body": "Body",
              "cta": "CTA Button", "offer_badge": "Offer Badge", "callout": "Callout", "stats_bar": "Stats Bar",
              "disclaimer": "Disclaimer"}


def font_style(weight: int, italic: bool) -> str:
    """Canonical style name; the plugin maps it to the closest style the family really has."""
    name = WEIGHT_NAMES.get(weight, "Regular")
    if italic:
        return "Italic" if name == "Regular" else f"{name} Italic"
    return name


def _ranges(block: TextBlock, fonts: dict[FontKind, str]) -> list[StyleRange]:
    """Words whose typeface, slant, weight or colour differ from the block's main style."""
    out: list[StyleRange] = []
    offset = 0
    base_kind, base_italic, base_weight = block.font_kind, block.italic, _weight(block)
    for line in block.lines:
        prev: StyleRange | None = None
        for i, word in enumerate(line.words):
            kind = line.word_kinds[i] if i < len(line.word_kinds) else line.font_kind
            italic = line.word_italics[i] if i < len(line.word_italics) else line.italic
            weight, color = line.word_weights[i], line.word_colors[i]
            r = StyleRange(start=offset + word.start, end=offset + word.start + len(word.text))
            # Only clear differences: text over photos gives noisy colour/stroke readings
            if color_distance(color, line.color) > 90 and color_distance(color, line.bg_color) > 90:
                r.color = color
            big = line.font_size >= 28  # weight readings on small text are too noisy to split a block
            if (kind, italic) != (base_kind, base_italic) or (big and abs(weight - base_weight) >= 300):
                w = weight if big and abs(weight - base_weight) >= 200 else base_weight
                r.font_family, r.font_style = fonts[kind], font_style(w, italic)
            if not (r.color or r.font_style):
                prev = None
                continue
            if prev and (prev.color, prev.font_family, prev.font_style) == (r.color, r.font_family, r.font_style):
                prev.end = r.end  # one range across "72 hrs", including the space
            else:
                out.append(r)
                prev = r
        offset += len(line.text) + 1  # "\n"
    return out


def _weight(block: TextBlock) -> int:
    return int(round(np.median([l.weight for l in block.lines]) / 100) * 100)


def _name(block: TextBlock, fallback: str, by_text: bool) -> str:
    """Ads: named by role ("Headline"). UIs: named by content, as designers do ("Edit profile")."""
    if not by_text:
        return ROLE_NAMES.get(block.role, fallback)
    first = block.lines[0].text.strip()
    return first if len(first) <= 40 else first[:39] + "…"


def _text_layer(block: TextBlock, fonts: dict[FontKind, str], scale: float, by_text: bool = False) -> Layer:
    s = lambda v: round(v / scale)
    weight = _weight(block)
    size = block.font_size
    line_h = block.line_height or size * 1.2
    # Figma centres glyphs in each line box, so put the first line box on the first OCR line's centre
    first = block.lines[0].box
    top = first.cy - line_h / 2
    return Layer(
        id=f"text-{block.id}", name=_name(block, "Text", by_text), role=block.role, type="text",
        x=s(block.box.x), y=s(top), w=s(block.box.w), h=s(line_h * len(block.lines)), fit_w=s(block.box.w),
        text=block.text, font_family=fonts[block.font_kind], font_style=font_style(weight, block.italic),
        font_size=max(1, s(size)), line_height=s(line_h), color=block.lines[0].color, align=block.align,
        ranges=_ranges(block, fonts),
    )


def build_layers(blocks: list[TextBlock], shapes: list[Shape], fonts: dict[FontKind, str], width: int,
                 height: int, subject_box=None, scale: float = 1.0, name_by_text: bool = False,
                 elements: list[Layer] | None = None, skip_blocks: set[int] | None = None) -> list[Layer]:
    """`elements`: UI cards / photos / buttons / icons (already laid out), drawn under the text.
    `skip_blocks`: text that already sits inside one of those buttons."""
    s = lambda v: round(v / scale)
    W, H = s(width), s(height)
    layers = [
        Layer(id="original", name="Original (reference)", role="background", type="image", x=0, y=0, w=W, h=H,
              src="original.png", visible=False, locked=True),
        Layer(id="background", name="Background", role="background", type="image", x=0, y=0, w=W, h=H,
              src="background.png"),
    ]
    if subject_box is not None:
        b = subject_box
        layers.append(Layer(id="subject", name="Subject", role="product", type="image",
                            x=s(b.x), y=s(b.y), w=s(b.w), h=s(b.h), src="subject.png"))

    layers += elements or []
    by_id = {b.id: b for b in blocks}
    in_button = set(skip_blocks or ())
    for i, shape in enumerate(shapes):
        b = shape.box
        live = [by_id[j] for j in shape.block_ids if not by_id[j].keep_in_background]
        if len(live) == 1:  # one text on a shape: auto-layout button / pill, text editable inside
            block = live[0]
            text = _text_layer(block, fonts, scale, name_by_text)
            pill = shape.corner_radius >= b.h * 0.4
            layers.append(text.model_copy(update=dict(
                id=f"button-{i}", name=_name(block, "Button", name_by_text), type="pill" if pill else "button",
                x=s(b.x), y=s(b.y), w=s(b.w), h=s(b.h), fill=shape.fill, corner_radius=s(shape.corner_radius),
                padding_x=s(max(0, block.box.x - b.x)), align="CENTER", line_height=0)))
            in_button.add(block.id)
        else:
            layers.append(Layer(id=f"shape-{i}", name="Shape", role="shape", type="rect", x=s(b.x), y=s(b.y),
                                w=s(b.w), h=s(b.h), fill=shape.fill, corner_radius=s(shape.corner_radius)))
    for block in blocks:
        if not block.keep_in_background and block.id not in in_button:
            layers.append(_text_layer(block, fonts, scale, name_by_text))
    return layers


def draw_overlay(img: np.ndarray, blocks: list[TextBlock], shapes: list[Shape], subject_box=None) -> Image.Image:
    """Original image with numbered boxes: red = editable text, grey = kept in the picture,
    blue = button/shape, green = subject cut-out."""
    im = Image.fromarray(img).convert("RGB")
    d = ImageDraw.Draw(im)
    lw = max(2, min(im.size) // 360)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", max(14, min(im.size) // 45))
    except OSError:
        font = ImageFont.load_default()
    if subject_box is not None:
        b = subject_box
        d.rectangle([b.x, b.y, b.x2, b.y2], outline=(22, 163, 74), width=lw)
    for sh in shapes:
        b = sh.box
        d.rectangle([b.x - lw, b.y - lw, b.x2 + lw, b.y2 + lw], outline=(37, 99, 235), width=lw)
    for bl in blocks:
        b = bl.box
        color = (150, 150, 150) if bl.keep_in_background else (230, 30, 60)
        d.rectangle([b.x, b.y, b.x2, b.y2], outline=color, width=lw)
        tag = str(bl.id)
        tw, th = d.textbbox((0, 0), tag, font=font)[2:]
        d.rectangle([b.x, b.y - th - 6, b.x + tw + 8, b.y], fill=color)
        d.text((b.x + 4, b.y - th - 4), tag, fill="white", font=font)
    return im
