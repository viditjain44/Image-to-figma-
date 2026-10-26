"""Find solid-colour rounded rectangles behind text: buttons, pills, badges, bars.

Flood-fills outwards from just around each text block; if the filled region is a
tidy rectangle a bit bigger than the text, it becomes its own Figma shape.
"""
import cv2
import numpy as np

from .. import config
from ..schemas import Box, Shape, TextBlock
from .text_style import hex_color


def _corner_radius(region: np.ndarray, box: Box) -> int:
    """Walk diagonally in from each corner until the fill starts: d = r(1 - 1/√2)."""
    sub = region[box.y:box.y2, box.x:box.x2]
    h, w = sub.shape
    steps = []
    for dy, dx, y0, x0 in ((1, 1, 0, 0), (1, -1, 0, w - 1), (-1, 1, h - 1, 0), (-1, -1, h - 1, w - 1)):
        d = 0
        while d < min(h, w) // 2 and not sub[y0 + dy * d, x0 + dx * d]:
            d += 1
        steps.append(d)
    r = float(np.median(steps)) / (1 - 2 ** -0.5)
    return int(min(r, h / 2))


def find(img: np.ndarray, blocks: list[TextBlock]) -> list[Shape]:
    H, W = img.shape[:2]
    tol = (config.COLOR_TOLERANCE,) * 3
    shapes: list[Shape] = []
    for block in blocks:
        if block.keep_in_background or any(block.id in s.block_ids for s in shapes):
            continue
        b, m = block.box, max(4, int(block.font_size * 0.3))
        seeds = [(b.x - m, int(b.cy)), (b.x2 + m, int(b.cy)), (int(b.cx), b.y - m), (int(b.cx), b.y2 + m)]
        seeds = [(x, y) for x, y in seeds if 0 <= x < W and 0 <= y < H]
        if len(seeds) < 4:
            continue
        colors = np.array([img[y, x] for x, y in seeds], np.float32)
        if np.abs(colors - np.median(colors, axis=0)).max() > config.COLOR_TOLERANCE * 2:
            continue  # not one solid colour all around the text

        mask = np.zeros((H + 2, W + 2), np.uint8)
        flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
        cv2.floodFill(img.copy(), mask, seeds[0], (0, 0, 0), tol, tol, flags)
        region = mask[1:-1, 1:-1] > 0
        contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        box = Box(x=x, y=y, w=w, h=h)
        filled = np.zeros((H, W), np.uint8)
        cv2.drawContours(filled, [c], -1, 255, -1)

        if (x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1   # touches the edge: it is the background
                or w * h > 0.35 * W * H
                or not box.contains(b, slack=2)
                or w > b.w + 8 * block.font_size or h > b.h * 3.2      # button-like padding, not a panel/card
                or cv2.contourArea(c) / (w * h) < 0.8):
            continue
        inside = [o for o in blocks if not o.keep_in_background and box.contains(o.box, slack=2)]
        if len(inside) != 1:
            continue  # a card / container with several texts stays in the picture as it is
        fill_rgb = np.median(img[region], axis=0)
        if not _clean(img, filled > 0, box, fill_rgb, block):
            continue  # icons or other artwork inside: keep the shape in the picture, only the text moves
        shapes.append(Shape(box=box, fill=hex_color(fill_rgb), corner_radius=_corner_radius(filled > 0, box),
                            block_ids=[block.id]))
    return shapes


def _clean(img: np.ndarray, shape: np.ndarray, box: Box, fill: np.ndarray, block: TextBlock) -> bool:
    """True when the shape holds only its fill and the text (no icon, avatar, chevron…)."""
    inner = cv2.erode(shape.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0  # skip the anti-aliased rim
    pad = int(block.font_size * 0.2)
    for line in block.lines:
        inner[max(0, line.box.y - pad):line.box.y2 + pad, max(0, line.box.x - pad):line.box.x2 + pad] = False
    if not inner.any():
        return True
    other = np.linalg.norm(img[inner].astype(np.float32) - fill, axis=1) > config.COLOR_TOLERANCE * 3
    return float(other.mean()) < 0.01


def rounded_mask(shape: Shape, H: int, W: int, grow: int = 0) -> np.ndarray:
    b, r = shape.box, max(0, shape.corner_radius)
    x0, y0, x1, y1 = b.x - grow, b.y - grow, b.x2 - 1 + grow, b.y2 - 1 + grow
    r = min(r + grow, (x1 - x0) // 2, (y1 - y0) // 2)
    mask = np.zeros((H, W), np.uint8)
    cv2.rectangle(mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return mask


def plan_erase(img: np.ndarray, shapes: list[Shape]) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    """How to remove each button from the picture.
    On a flat surround (most UIs) fill it with that colour: inpainting a big solid block smears.
    Returns ([(mask, colour)] flat fills, mask of the shapes that still need inpainting)."""
    H, W = img.shape[:2]
    fills, leftover = [], np.zeros((H, W), np.uint8)
    for s in shapes:
        mask = rounded_mask(s, H, W, grow=2)
        ring = (rounded_mask(s, H, W, grow=8) > 0) & (rounded_mask(s, H, W, grow=4) == 0)
        px = img[ring].astype(np.float32)
        if len(px) and np.abs(px - np.median(px, axis=0)).mean() < 6:
            fills.append((mask, np.median(px, axis=0).astype(np.uint8)))
        else:
            leftover |= mask
    return fills, leftover


def find_tags(img: np.ndarray, blocks: list[TextBlock], taken: list[Shape]) -> list[Shape]:
    """Label tags on photos: a line of text on a tinted band (often semi-transparent, so the photo
    shows through and flood fill can't see it). Per line: the band's non-glyph pixels differ clearly
    from the surroundings (other text ignored) and are more uniform than the photo around them; the
    band is grown row by row while the colour still matches. A tagged line inside a multi-line
    block is split off into its own block (`blocks` is extended in place) so it can become a pill."""
    from .text_style import _colors, glyph_alpha

    H, W = img.shape[:2]
    f = img.astype(np.float32)
    all_text = np.zeros((H, W), bool)
    for block in blocks:
        for line in block.lines:
            all_text[line.box.y:line.box.y2, line.box.x:line.box.x2] = True
    used = {i for s in taken for i in s.block_ids}
    out: list[Shape] = []
    for block in list(blocks):
        if block.keep_in_background or block.id in used:
            continue
        for line in list(block.lines):
            b = line.box
            text_rgb, bg_rgb, _ = _colors(img, b)
            inner = glyph_alpha(img, b, text_rgb, bg_rgb) < 0.15
            if inner.sum() < 50:
                continue
            inside_px = f[b.y:b.y2, b.x:b.x2][inner]
            band = inside_px.mean(axis=0)
            m = int(line.font_size * 1.2)
            x0, y0, x1, y1 = max(0, b.x - m), max(0, b.y - m), min(W, b.x2 + m), min(H, b.y2 + m)
            ring = ~all_text[y0:y1, x0:x1]
            if ring.sum() < 100:
                continue
            outside_px = f[y0:y1, x0:x1][ring]
            outside = outside_px.mean(axis=0)
            # A tag changes the hue (navy over brown hair). Text straight on the picture only sees the
            # same photo a bit darker or lighter, so drop the brightness part of the difference.
            shift = band - outside
            hue_shift = float(np.linalg.norm(shift - shift.mean()))
            if hue_shift < 10 or inside_px.std(axis=0).mean() > 0.7 * outside_px.std(axis=0).mean():
                continue  # no band: the text sits straight on the picture

            def is_band(row: np.ndarray) -> bool:
                c = np.median(row.reshape(-1, 3), axis=0)  # median: an icon in the band doesn't stop it
                return np.linalg.norm(c - band) < np.linalg.norm(c - outside)

            top, bot, left, right = b.y, b.y2, b.x, b.x2
            limit = int(line.font_size * 0.8)
            while top > 0 and b.y - top < limit and is_band(f[top - 1, left:right]):
                top -= 1
            while bot < H and bot - b.y2 < limit and is_band(f[bot, left:right]):
                bot += 1
            while left > 0 and b.x - left < limit and is_band(f[top:bot, left - 1]):
                left -= 1
            while right < W and right - b.x2 < limit and is_band(f[top:bot, right]):
                right += 1
            box = Box(x=left, y=top, w=right - left, h=bot - top)
            if any(min(box.x2, o.box.x2) > max(box.x, o.box.x) and min(box.y2, o.box.y2) > max(box.y, o.box.y)
                   for o in out):
                continue
            # Only a band holding nothing but its text is separated (like a clean button); with an
            # icon on it, it stays in the picture and just the text is lifted.
            area = f[box.y:box.y2, box.x:box.x2]
            other = np.linalg.norm(area - band, axis=2) > 60
            pad = max(2, int(line.font_size * 0.15))
            other[max(0, b.y - pad - box.y):b.y2 + pad - box.y, max(0, b.x - pad - box.x):b.x2 + pad - box.x] = False
            other &= ~all_text[box.y:box.y2, box.x:box.x2]  # a neighbouring line touching the band is fine
            # count only marks lying wholly on the band (an icon); arrows or lines crossing its edge
            # belong to the picture around it
            n_o, lab_o, st_o, _ = cv2.connectedComponentsWithStats(other.astype(np.uint8), connectivity=8)
            inside_marks = sum(int(st_o[k, cv2.CC_STAT_AREA]) for k in range(1, n_o)
                               if st_o[k, 0] > 0 and st_o[k, 1] > 0
                               and st_o[k, 0] + st_o[k, 2] < box.w and st_o[k, 1] + st_o[k, 3] < box.h)
            if inside_marks > 0.02 * box.w * box.h:
                continue
            # ...and the band really ends at its edges. If it carries on past one, growing stopped at
            # something on it (an icon at a pill's end) and lifting the rest would leave a stub.
            hue_dir = (shift - shift.mean()) / (hue_shift + 1e-6)

            def continues(strip: np.ndarray) -> bool:
                # judged by hue like the band itself: dark hair is not "band" just for being dark
                px = strip.reshape(-1, 3) - outside
                if not len(px):
                    return False
                t = ((px - px.mean(axis=1, keepdims=True)) @ hue_dir) / hue_shift
                return (t > 0.8).mean() > 0.4  # band pixels score ~1.2, deep hair shadow ~0.5
            mid = slice(box.y + box.h // 4, box.y2 - box.h // 4)
            if (continues(f[mid, max(0, box.x - 4):box.x - 1]) or continues(f[mid, box.x2 + 1:box.x2 + 4])):
                continue
            owner = block
            if len(block.lines) > 1:  # the tag belongs to this line only: make it a block of its own
                owner = _split_line(blocks, block, line)
            out.append(Shape(box=box, fill=hex_color(band), corner_radius=0, block_ids=[owner.id]))
    return out


def _split_line(blocks: list[TextBlock], block: TextBlock, line) -> TextBlock:
    rest = [l for l in block.lines if l is not line]
    block.lines = rest
    box = rest[0].box
    for l in rest[1:]:
        box = box.union(l.box)
    block.box = box
    if len(rest) == 1:
        block.line_height = 0
    new = block.model_copy(update={"id": max(b.id for b in blocks) + 1, "lines": [line], "box": line.box,
                                   "line_height": 0})
    blocks.append(new)
    return new
