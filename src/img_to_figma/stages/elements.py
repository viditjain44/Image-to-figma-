"""Split a UI screenshot into design elements: cards/panels/inputs, buttons (with icons), photos and
icons/logos, each its own layer, plus a background with all of them removed.

Interfaces are drawn in exact flat colours, which makes this tractable:
- a card is a large region of one exact colour with a (rounded) rectangular outline; a 1-2 px ring of
  another uniform colour around it is its border (input fields are cards whose fill equals the parent's);
- a card holding exactly one label, sized like a button, becomes an auto-layout button; an icon inside
  it is embedded next to the label;
- photo tiles come from `photos.tile_mask`;
- everything else that differs from the surface it sits on (icons, logos, dividers, graphics, text that
  stays a picture) becomes a transparent image, "un-blended" from that surface so it sits cleanly on
  any colour. Inside a card *everything* is lifted, so moving the card never leaves content behind.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..engines import lama
from ..schemas import Box, FontKind, Layer, TextBlock
from . import compose, photos
from .shapes import _corner_radius
from .text_style import hex_color

log = logging.getLogger(__name__)


@dataclass
class Card:
    box: Box
    filled: np.ndarray        # bool mask of the card (holes filled)
    colour: np.ndarray        # RGB
    radius: int
    stroke: np.ndarray | None = None
    parent: "Card | None" = None
    block_ids: list[int] = field(default_factory=list)

    @property
    def area(self) -> int:
        return self.box.w * self.box.h


@dataclass
class Decomposition:
    layers: list[Layer]                 # cards, photos, buttons, icons (bottom to top); text is added after
    background: np.ndarray
    mask: np.ndarray                    # everything lifted off the picture
    button_blocks: set[int]
    counts: dict[str, int]


def _packed(img: np.ndarray) -> np.ndarray:
    a = img.astype(np.int32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def _ring(mask: np.ndarray, inner: int, outer: int) -> np.ndarray:
    k = lambda n: np.ones((2 * n + 1, 2 * n + 1), np.uint8)
    m = mask.astype(np.uint8)
    return (cv2.dilate(m, k(outer)) > 0) & ~(cv2.dilate(m, k(inner)) > 0) if inner > 0 else \
        (cv2.dilate(m, k(outer)) > 0) & ~mask


def _find_cards(img: np.ndarray, packed: np.ndarray, page: int, photo: np.ndarray) -> list[Card]:
    H, W = img.shape[:2]
    side = min(H, W)
    min_area = (0.025 * side) ** 2
    colours, counts = np.unique(packed, return_counts=True)
    cards: list[Card] = []
    for c in colours[(counts >= min_area)]:
        mask = (packed == c).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # bridge 1-2 px dividers
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < min_area or w < 0.02 * side or h < 0.015 * side or w * h > 0.9 * H * W:
                continue
            comp = (labels == i).astype(np.uint8)
            contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            filled = np.zeros((H, W), np.uint8)
            cv2.drawContours(filled, contours, -1, 1, -1)
            if filled.sum() / (w * h) < 0.85:
                continue  # not a rectangle: the gaps between other things
            if c == page and (x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1):
                continue  # the page itself
            if photo[y + h // 2, x + w // 2]:
                continue  # the white backdrop of a product shot
            box = Box(x=int(x), y=int(y), w=int(w), h=int(h))
            colour = np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255])
            cards.append(Card(box=box, filled=filled > 0, colour=colour, radius=_corner_radius(filled > 0, box)))
    cards.sort(key=lambda k: -k.area)
    return cards


def _nest_and_borders(img: np.ndarray, cards: list[Card], page_rgb: np.ndarray) -> list[Card]:
    kept: list[Card] = []
    for card in cards:
        cy, cx = int(card.box.cy), int(card.box.cx)
        card.parent = next((p for p in reversed(kept) if p.filled[cy, cx] and p.box.contains(card.box, slack=2)), None)
        parent_rgb = card.parent.colour if card.parent else page_rgb
        ring = _ring(card.filled, 0, 2)
        px = img[ring].astype(np.float32)
        if len(px):
            med = np.median(px, axis=0)
            if np.abs(px - med).max(axis=1).mean() < 25 and np.abs(med - parent_rgb).max() > 15 \
                    and np.abs(med - card.colour).max() > 15:
                card.stroke = med
        if np.abs(card.colour.astype(int) - parent_rgb.astype(int)).max() > 4 or card.stroke is not None:
            kept.append(card)
    return kept


def _unblend(rgb: np.ndarray, bg: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """RGBA of ink drawn over a known background colour: alpha from how far each pixel is from the
    background, colour recovered as if the ink were drawn on transparent."""
    p = rgb.astype(np.float32)
    b = bg.astype(np.float32)
    d = np.linalg.norm(p - b, axis=-1)
    full = max(20.0, float(np.percentile(d[mask], 90))) if mask.any() else 255.0
    alpha = np.clip(d / full, 0, 1) * mask
    safe = np.maximum(alpha, 1e-3)[..., None]
    colour = np.clip(b + (p - b) / safe, 0, 255)
    return np.dstack([colour, alpha * 255]).astype(np.uint8)


def decompose(img: np.ndarray, blocks: list[TextBlock], text_mask: np.ndarray, out: Path,
              fonts: dict[FontKind, str], scale: float, by_text: bool) -> Decomposition:
    H, W = img.shape[:2]
    side = min(H, W)
    s = lambda v: round(v / scale)
    packed = _packed(img)
    colours, counts = np.unique(packed, return_counts=True)
    page = int(colours[counts.argmax()])
    page_rgb = np.array([(page >> 16) & 255, (page >> 8) & 255, page & 255])
    photo = photos.tile_mask(img).astype(bool)

    cards = _nest_and_borders(img, _find_cards(img, packed, page, photo), page_rgb)
    by_id = {b.id: b for b in blocks}
    editable = [b for b in blocks if not b.keep_in_background]
    for b in editable:  # each text belongs to the smallest card around it
        home = next((c for c in reversed(cards) if c.box.contains(b.box, slack=2) and c.filled[int(b.box.cy), int(b.box.cx)]), None)
        if home:
            home.block_ids.append(b.id)

    # What each pixel should look like with nothing on it: its card's colour, else the page colour
    expected = np.empty_like(img)
    expected[:] = page_rgb
    for card in cards:  # largest first, so nested cards paint over their parents
        expected[card.filled] = card.colour
    edge = np.zeros((H, W), bool)
    for card in cards:  # anti-aliased card edges and borders belong to the card layer
        m = card.filled.astype(np.uint8)
        edge |= (cv2.dilate(m, np.ones((5, 5), np.uint8)) > 0) & ~(cv2.erode(m, np.ones((5, 5), np.uint8)) > 0)
    # Editable text owns its whole line box (soft glyph edges included): never part of an icon
    text_zone = cv2.dilate(text_mask, np.ones((3, 3), np.uint8)) > 0
    for b in editable:
        for line in b.lines:
            pad = max(2, round(line.font_size * 0.12))
            text_zone[max(0, line.box.y - pad):line.box.y2 + pad, max(0, line.box.x - pad):line.box.x2 + pad] = True
    diff = np.abs(img.astype(np.int16) - expected.astype(np.int16)).max(axis=2)
    ink = (diff > 10) & ~photo & ~edge & ~text_zone
    in_card = np.zeros((H, W), bool)
    for card in cards:
        in_card |= card.filled

    # ---- icons, logos, graphics ----
    k = max(3, round(0.004 * side)) | 1
    merged = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    icons: list[tuple[Box, np.ndarray, str]] = []  # box, own mask, name
    lifted = np.zeros((H, W), bool)
    multicolor_boxes = [b.box for b in blocks if any(l.multicolor for l in b.lines)]
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        comp = (labels == i) & ink
        if comp.sum() < 12:
            continue
        contrast = float(diff[comp].max())
        if not in_card[y + h // 2, x + w // 2] and contrast < 30 and area > (0.05 * side) ** 2:
            continue  # a soft shadow or tint on the page: leave it in the picture
        box = Box(x=int(x), y=int(y), w=int(w), h=int(h))
        logo = any(mb.contains(box, slack=4) or box.contains(mb, slack=4) for mb in multicolor_boxes)
        name = "Logo" if logo else ("Icon" if max(w, h) <= 0.06 * side else "Graphic")
        icons.append((box, comp, name))
        lifted |= comp

    layers: list[Layer] = []
    button_blocks: set[int] = set()
    embedded: set[int] = set()  # icons that went into a button

    # ---- buttons: a card with exactly one label, sized like a button ----
    buttons: list[tuple[Card, TextBlock]] = []
    for card in cards:
        if len(card.block_ids) != 1:
            continue
        b = by_id[card.block_ids[0]]
        fs = b.font_size
        if card.box.h <= b.box.h * 3.2 and card.box.w <= b.box.w + 10 * fs:
            buttons.append((card, b))
    button_cards = {id(c) for c, _ in buttons}

    def same_place(a: Box, b: Box) -> bool:
        ix = max(0, min(a.x2, b.x2) - max(a.x, b.x)) * max(0, min(a.y2, b.y2) - max(a.y, b.y))
        return ix > 0.7 * min(a.w * a.h, b.w * b.h)
    cards = [c for c in cards if id(c) in button_cards
             or not any(same_place(c.box, bc.box) for bc, _ in buttons)]

    # ---- assets ----
    def save(name: str, rgba: np.ndarray) -> str:
        Image.fromarray(rgba, "RGBA").save(out / name)
        return name

    for card in cards:
        if id(card) in button_cards:
            continue
        b = card.box
        touches = b.x <= 1 or b.y <= 1 or b.x2 >= W - 1 or b.y2 >= H - 1
        parent_rgb = card.parent.colour if card.parent else page_rgb
        name = "Panel" if touches else "Input" if card.stroke is not None and \
            np.abs(card.colour.astype(int) - parent_rgb.astype(int)).max() <= 4 else "Card"
        layers.append(Layer(
            id=f"card-{len(layers)}", name=name, role="card", type="rect", x=s(b.x), y=s(b.y), w=s(b.w), h=s(b.h),
            fill=hex_color(card.colour), corner_radius=s(card.radius),
            stroke=hex_color(card.stroke) if card.stroke is not None else "", stroke_width=1 if card.stroke is not None else 0))

    pn, plabels, pstats, _ = cv2.connectedComponentsWithStats(photo.astype(np.uint8), connectivity=4)
    for i in range(1, pn):
        x, y, w, h, _ = pstats[i]
        m = plabels[y:y + h, x:x + w] == i
        rgba = np.dstack([img[y:y + h, x:x + w], (m * 255).astype(np.uint8)])
        src = save(f"photo-{i}.png", rgba)
        layers.append(Layer(id=f"photo-{i}", name="Image", role="photo", type="image",
                            x=s(x), y=s(y), w=s(w), h=s(h), src=src))
        lifted |= plabels == i

    for card, block in buttons:
        b = card.box
        text = compose._text_layer(block, fonts, scale, by_text)
        layer = text.model_copy(update=dict(
            id=f"button-{len(layers)}", name=compose._name(block, "Button", by_text),
            type="pill" if card.radius >= b.h * 0.4 else "button", role="cta",
            x=s(b.x), y=s(b.y), w=s(b.w), h=s(b.h), fill=hex_color(card.colour), corner_radius=s(card.radius),
            stroke=hex_color(card.stroke) if card.stroke is not None else "", stroke_width=1 if card.stroke is not None else 0,
            padding_x=s(max(0, block.box.x - b.x)), align="CENTER", line_height=0))
        inside = [j for j, (ib, _, _) in enumerate(icons) if b.contains(ib, slack=1)]
        if len(inside) == 1:  # "+ Add Hidden Rate": the icon goes inside the button
            j = inside[0]
            ib, comp, _ = icons[j]
            rgba = _unblend(img[ib.y:ib.y2, ib.x:ib.x2], card.colour, comp[ib.y:ib.y2, ib.x:ib.x2])
            left = ib.cx < block.box.cx
            gap = (block.box.x - ib.x2) if left else (ib.x - block.box.x2)
            layer = layer.model_copy(update=dict(
                icon_src=save(f"button-icon-{j}.png", rgba), icon_w=s(ib.w), icon_h=s(ib.h),
                icon_side="left" if left else "right", gap=s(max(0, gap)),
                padding_x=s(max(0, min(ib.x, block.box.x) - b.x))))
            embedded.add(j)
        layers.append(layer)
        button_blocks.add(block.id)

    for j, (box, comp, name) in enumerate(icons):
        if j in embedded:
            continue
        crop = (slice(box.y, box.y2), slice(box.x, box.x2))
        rgba = _unblend(img[crop], expected[crop], comp[crop])
        layers.append(Layer(id=f"icon-{j}", name=name, role="logo" if name == "Logo" else "icon", type="image",
                            x=s(box.x), y=s(box.y), w=s(box.w), h=s(box.h), src=save(f"icon-{j}.png", rgba)))

    # ---- background: paint what is known, inpaint only what isn't ----
    # Under a lifted card is the page (every card is its own layer now), and so is its soft drop shadow.
    # Lifted things on the page are replaced by the page colour when that is what surrounds them.
    # Only things on surfaces we don't know (gradients, photos) are inpainted.
    background = img.copy()
    covered = np.zeros((H, W), bool)
    a16 = img.astype(np.int16)
    smooth = np.zeros((H, W), bool)  # gradient pixels (a shadow), as opposed to one exact flat colour
    smooth[:, 1:] = np.abs(a16[:, 1:] - a16[:, :-1]).max(axis=2) > 0
    reach = max(3, round(0.012 * side))
    for card in cards:
        under = cv2.dilate(card.filled.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        covered |= under
        shadow = _ring(under, 0, reach)
        tint = np.abs(img.astype(np.int16) - page_rgb).max(axis=2)
        darker = img.astype(np.int16).sum(axis=2) <= int(page_rgb.sum())
        covered |= shadow & (tint <= 40) & darker & smooth  # a soft shadow of the card on the page
    background[covered] = page_rgb
    rest = (lifted | (text_mask > 0)) & ~covered
    rest = cv2.dilate(rest.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    n_rest, rlabels = cv2.connectedComponents(rest.astype(np.uint8))
    unknown = np.zeros((H, W), np.uint8)
    for i in range(1, n_rest):
        comp = rlabels == i
        ring = _ring(comp, 1, 4) & ~covered & ~rest
        px = img[ring].astype(np.int16)
        if len(px) and (np.abs(px - page_rgb).max(axis=1) <= 3).mean() > 0.9:
            background[comp] = page_rgb
        else:
            unknown[comp] = 255
    if unknown.any():
        filled, _ = lama.inpaint(background, unknown)
        background[unknown > 0] = filled[unknown > 0]
    erase = ((covered | rest) * 255).astype(np.uint8)

    counts = {"cards": len(cards) - len(buttons), "buttons": len(buttons), "photos": pn - 1,
              "icons": len(icons) - len(embedded)}
    return Decomposition(layers=layers, background=background, mask=erase, button_blocks=button_blocks, counts=counts)


def lift_photos(img: np.ndarray, clean: np.ndarray, out: Path, scale: float) -> tuple[np.ndarray, list[Layer], float]:
    """For ads/posters that have flat areas: each photo panel or product shot becomes an image layer
    (cut from `clean`, so editable text isn't baked in twice) and is removed from the background.
    Returns (background, layers, share of the image covered by photos)."""
    H, W = img.shape[:2]
    tiles = photos.tile_mask(img)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(tiles, connectivity=4)
    layers: list[Layer] = []
    erase = np.zeros((H, W), np.uint8)
    s = lambda v: round(v / scale)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w * h > 0.9 * H * W:
            continue  # the whole picture: nothing to separate
        m = labels[y:y + h, x:x + w] == i
        rgba = np.dstack([clean[y:y + h, x:x + w], (m * 255).astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(out / f"photo-{i}.png")
        layers.append(Layer(id=f"photo-{i}", name="Image", role="photo", type="image",
                            x=s(x), y=s(y), w=s(w), h=s(h), src=f"photo-{i}.png"))
        erase[labels == i] = 255
    if not layers:
        return clean, [], 0.0
    erase = cv2.dilate(erase, np.ones((5, 5), np.uint8))
    background = clean.copy()
    unknown = np.zeros((H, W), np.uint8)
    n2, regions = cv2.connectedComponents(erase)
    for i in range(1, n2):
        region = regions == i
        ring = _ring(region, 1, 6) & (erase == 0)
        px = clean[ring].astype(np.int16)
        if len(px):
            med = np.median(px, axis=0)
            if (np.abs(px - med).max(axis=1) <= 8).mean() >= 0.5:  # mostly one flat colour around it (a white page)
                background[region] = med.astype(np.uint8)
                continue
        unknown[region] = 255
    if unknown.any():
        filled, _ = lama.inpaint(clean, unknown)
        background[unknown > 0] = filled[unknown > 0]
    return background, layers, float((erase > 0).mean())
