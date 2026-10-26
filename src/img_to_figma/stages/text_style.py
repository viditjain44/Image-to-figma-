"""Measure how each OCR line looks: text colour, background colour, font size, weight.

Also builds the pixel mask of the glyphs, which the inpaint stage erases.
"""
import cv2
import numpy as np

from ..schemas import Box, OcrLine, StyledLine

TALL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789bdfhijklt!?%&#@$/\\()[]{}|\"'“”‘’")
DESCENDERS = set("gjpqyQ")
# Share of the em box above the baseline / below it (Inter, Poppins, Helvetica are all close)
CAP_HEIGHT, X_HEIGHT, DESCENT = 0.72, 0.52, 0.21


def hex_color(rgb) -> str:
    r, g, b = (int(round(float(c))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def color_distance(a: str, b: str) -> float:
    pa = np.array([int(a[i:i + 2], 16) for i in (1, 3, 5)], float)
    pb = np.array([int(b[i:i + 2], 16) for i in (1, 3, 5)], float)
    return float(np.linalg.norm(pa - pb))


def _clip(box: Box, pad: int, w: int, h: int) -> tuple[int, int, int, int]:
    return max(0, box.x - pad), max(0, box.y - pad), min(w, box.x2 + pad), min(h, box.y2 + pad)


def _colors(img: np.ndarray, box: Box) -> tuple[np.ndarray, np.ndarray, float]:
    """(text colour, background colour, background noise) for the pixels inside `box`."""
    H, W = img.shape[:2]
    m = max(3, box.h // 6)
    x0, y0, x1, y1 = _clip(box, m, W, H)
    outer = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    inner = img[box.y:box.y2, box.x:box.x2].reshape(-1, 3).astype(np.float32)
    if len(inner) < 4:
        return np.zeros(3), np.full(3, 255.0), 0.0
    ring_mask = np.ones((y1 - y0, x1 - x0), bool)
    ring_mask[box.y - y0:box.y2 - y0, box.x - x0:box.x2 - x0] = False
    ring = img[y0:y1, x0:x1][ring_mask].astype(np.float32)
    # Background = the box's own 2 px edge: glyphs rarely fill it, and unlike a ring outside the box
    # it stays on a tight pill/button instead of reaching the photo behind it
    crop = img[box.y:box.y2, box.x:box.x2]
    if min(crop.shape[:2]) > 6:
        edge = np.concatenate([crop[:2].reshape(-1, 3), crop[-2:].reshape(-1, 3),
                               crop[:, :2].reshape(-1, 3), crop[:, -2:].reshape(-1, 3)]).astype(np.float32)
        bg = np.median(edge, axis=0)
    else:
        bg = np.median(ring if len(ring) else outer, axis=0)
    # Text = the pixels inside the box that differ most from the background (skips anti-aliasing)
    dist = np.linalg.norm(inner - bg, axis=1)
    differs = dist > 30  # thin text can be < 15% of the box, so look only at pixels that differ at all
    if differs.sum() >= 40:
        # Text is one flat colour; a photo behind it is not. Of two colour clusters, take the tighter one.
        px = inner[differs]
        _, labels, centers = cv2.kmeans(px, 2, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                        3, cv2.KMEANS_PP_CENTERS)
        labels = labels.ravel()
        best, best_spread = None, np.inf
        for k in range(2):
            members = px[labels == k]
            if len(members) < 0.2 * len(px):
                continue
            spread = float(np.linalg.norm(members - centers[k], axis=1).mean())
            if spread < best_spread:
                best, best_spread = members, spread
        far = best if best is not None else px
        d = np.linalg.norm(far - bg, axis=1)
        far = far[d >= np.percentile(d, 50)]  # the solid core of the glyphs, not their soft edges
    elif differs.sum() >= 5:
        far = inner[differs]
    else:
        far = inner[dist >= np.percentile(dist, 85)]
    text = np.median(far, axis=0) if len(far) else np.zeros(3)
    noise = float(np.linalg.norm(ring - bg, axis=1).mean()) if len(ring) else 0.0
    return text, bg, noise


ICON_CHARS = set("<>|/\\✓✔✗✘•·◦▪▸►›»→←↗-–—+*~^")


def glyph_alpha(img: np.ndarray, box: Box, text: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Soft glyph coverage 0..1 (same size as `box`): 1 = text colour, 0 = background.
    Keeping the anti-aliased edge makes stroke measurements sub-pixel accurate."""
    crop = img[box.y:box.y2, box.x:box.x2].astype(np.float32)
    axis = text - bg
    norm = float(axis @ axis)
    if norm < 400:  # text and background almost the same colour: fall back to the whole box
        return np.ones(crop.shape[:2], np.float32)
    return np.clip(((crop - bg) @ axis) / norm, 0, 1)


def _font_size(mask: np.ndarray, text: str, box_h: int) -> float:
    frac = mask.mean(axis=1)
    # Rows that are nearly all "ink" are a shape/photo edge clipped by the box, not glyphs
    rows = np.where((frac > 0.004) & (frac < 0.7))[0]
    if len(rows) > 1:  # keep the main run of rows (allowing i-dot / accent gaps)
        gap = max(2, int(box_h * 0.18))
        runs, start = [], rows[0]
        for a, b in zip(rows, rows[1:]):
            if b - a > gap:
                runs.append((start, a))
                start = b
        runs.append((start, rows[-1]))
        lo, hi = max(runs, key=lambda r: r[1] - r[0])
        rows = rows[(rows >= lo) & (rows <= hi)]
    chars = set(text)
    top = CAP_HEIGHT if chars & TALL else X_HEIGHT
    bottom = DESCENT if chars & DESCENDERS else 0.0
    from_box = box_h * 0.95  # Vision's line box is close to the em box
    if len(rows) < 2:
        return from_box
    from_ink = (rows[-1] - rows[0] + 1) / (top + bottom)
    # Swashes, curly quotes and italic descenders make the ink read too big: trust the box then.
    # Vision's box is sometimes padded (text on pills), so a smaller ink reading wins.
    return from_ink if 0.45 <= from_ink / from_box <= 1.2 else from_box


def _weight(alpha: np.ndarray, font_size: float, serif: bool = False) -> int:
    """Weight from stroke thickness / font size. Stroke = 2 × ink area / outline length, which is
    stable from 20 px to 100 px (calibrated on Poppins Light…Bold; Inter is within a step)."""
    solid = (alpha > 0.5).astype(np.uint8)
    if solid.sum() < 20 or font_size <= 0:
        return 400
    contours, _ = cv2.findContours(solid, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    r = 2 * float(alpha.sum()) / max(perimeter, 1.0) / font_size
    if serif:
        r += 0.02  # high-contrast serifs have thin hairlines that pull the average down
    for limit, weight in ((0.074, 300), (0.094, 400), (0.114, 500), (0.135, 600), (0.16, 700), (0.185, 800)):
        if r < limit:
            return weight
    return 900


def _slant(mask: np.ndarray) -> tuple[float, float]:
    """(shear that makes strokes most vertical, how much sharper the column profile gets)."""
    h = mask.shape[0]
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return 0.0, 1.0
    scores = {}
    for s in np.round(np.linspace(-0.15, 0.45, 25), 3):
        xp = np.round(xs - s * (h - 1 - ys)).astype(int)
        scores[s] = float((np.bincount(xp - xp.min()).astype(np.float64) ** 2).sum())
    best = max(scores, key=scores.get)
    return float(best), scores[best] / scores[0.0]


def _stroke_contrast(mask: np.ndarray) -> float:
    """Thick/thin stroke ratio along the glyph centre lines: ~1-1.5 for sans, 2.5+ for display serifs."""
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    v = dt[(dt >= cv2.dilate(dt, np.ones((3, 3)))) & (dt > 0.7)]
    if len(v) < 10:
        return 1.0
    return float(np.percentile(v, 90) / max(np.percentile(v, 25), 0.5))


def _typeface(mask: np.ndarray, font_size: float) -> tuple[str, bool]:
    """(kind, italic) for one word. Small text is too coarse to judge: (sans, upright)."""
    if font_size < 20:
        return "sans", False
    shear, gain = _slant(mask)
    italic = shear >= 0.15 and gain >= 1.12
    contrast = _stroke_contrast(mask) if font_size >= 28 else 1.0
    serif = contrast >= 2.4 or (italic and contrast >= 2.0)
    return ("serif" if serif else "sans"), italic


def _trim_icons(mask: np.ndarray, line: OcrLine, font_size: float) -> int:
    """Vision often includes a leading icon (✓, •, →) in the first word's box.
    Letters inside one word sit < 0.15em apart, so a gap over ~0.22em means an icon before the text."""
    if not line.words:
        return 0
    first = line.words[0].box
    x_end = min(first.x2 - line.box.x, mask.shape[1])
    cols = mask[:, :x_end].any(axis=0)
    gap_needed = max(3, int(font_size * 0.22))
    run, last_ink_end = 0, -1
    for i, ink in enumerate(cols):
        if ink:
            if last_ink_end >= 0 and run >= gap_needed:
                return i  # text starts after this gap
            run = 0
            last_ink_end = i
        else:
            run += 1
    return 0


def _drop_first_word(line: OcrLine) -> OcrLine:
    w = line.words
    start = w[1].start
    words = [word.model_copy(update={"start": word.start - start}) for word in w[1:]]
    box = Box(x=w[1].box.x, y=line.box.y, w=max(1, line.box.x2 - w[1].box.x), h=line.box.h)
    return line.model_copy(update={"text": line.text[start:], "box": box, "words": words})


def _leading_icon(line: OcrLine, mask: np.ndarray, font_size: float) -> bool:
    """Vision reads UI icons as short words: magnifier -> 'Q', star -> '#', people -> '83'.
    Interfaces leave more room after an icon (0.5-0.9em) than between words (0.25-0.47em)."""
    w = line.words
    if len(w) < 2:
        return False
    first = w[0].text
    if len(first) == 1 and first in ICON_CHARS:
        return True
    if not (len(first) <= 2 or (len(first) <= 3 and not any(c.isalpha() for c in first))):
        return False
    cols = mask.any(axis=0)
    inks = []
    for word in w:
        x0, x1 = max(0, word.box.x - line.box.x), min(len(cols), word.box.x2 - line.box.x)
        idx = np.nonzero(cols[x0:x1])[0]
        if not len(idx):
            return False
        inks.append((x0 + idx[0], x0 + idx[-1]))
    gaps = [(b[0] - a[1]) / font_size for a, b in zip(inks, inks[1:])]
    rest = [g for g in gaps[1:] if g > 0]
    if rest:
        return gaps[0] >= 0.45 and gaps[0] >= 1.3 * float(np.median(rest))
    return gaps[0] >= 0.5


def _strip_pill_border(line: OcrLine) -> OcrLine:
    """The left edge of an outlined pill is often read as '(' / '[' / '|': "(Public" -> "Public"."""
    if len(line.text) < 3 or line.text[0] not in "([|" or (line.text[0] == "(" and ")" in line.text) \
            or (line.text[0] == "[" and "]" in line.text):
        return line
    words = [w.model_copy(update={"start": max(0, w.start - 1), "text": w.text[1:] if i == 0 else w.text})
             for i, w in enumerate(line.words)]
    if words and not words[0].text:
        words = words[1:]
    return line.model_copy(update={"text": line.text[1:], "words": words})


_NARROW = set(".,:;'!|il1()[]")


def _trim_glued_stars(img: np.ndarray, line: OcrLine) -> OcrLine:
    """Vision gives "4.7" the box of the whole "4.7*****". Keep the ink clusters from the left until
    they are about as wide as the text should be, and cut at the next gap: the stars stay put."""
    if not line.glued_stars:
        return line
    text_rgb, bg_rgb, _ = _colors(img, line.box)
    cols = (glyph_alpha(img, line.box, text_rgb, bg_rgb) > 0.45).any(axis=0)
    fs = line.box.h * 0.75
    expected = fs * sum(0.28 if c in _NARROW or c == " " else 0.58 for c in line.text)
    wide_gap = max(4, int(line.box.h * 0.25))  # the space before the stars; gaps inside "4.1" are smaller
    segments, x = [], 0
    while x < len(cols):
        if cols[x]:
            start = x
            while x < len(cols) and cols[x]:
                x += 1
            segments.append((start, x))
        x += 1
    end = None
    for (s0, e0), nxt in zip(segments, segments[1:] + [None]):
        end = e0
        if e0 >= expected * 0.5 and (nxt is None or nxt[0] - e0 >= wide_gap):
            break
    if end is None or end >= len(cols) - 2:
        return line
    cut = min(len(cols), end + 2)
    box = Box(x=line.box.x, y=line.box.y, w=cut, h=line.box.h)
    words = [w.model_copy(update={"box": Box(x=w.box.x, y=w.box.y, w=max(1, min(w.box.x2, box.x2) - w.box.x),
                                                h=w.box.h)}) for w in line.words]
    return line.model_copy(update={"box": box, "words": words, "glued_stars": False})


def _measure(img: np.ndarray, line: OcrLine):
    text_rgb, bg_rgb, noise = _colors(img, line.box)
    alpha = glyph_alpha(img, line.box, text_rgb, bg_rgb)
    mask = alpha > 0.45
    return text_rgb, bg_rgb, noise, alpha, mask, _font_size(mask, line.text, line.box.h)


def _multicolor(img: np.ndarray, box: Box, bg_rgb: np.ndarray) -> bool:
    """Glyphs in two or more clearly different hues (the Google logo, rainbow wordmarks) are artwork.
    Grey, black or single-colour text, anti-aliased edges included, has at most one hue."""
    px = img[box.y:box.y2, box.x:box.x2].reshape(-1, 3)
    ink = px[np.linalg.norm(px.astype(np.float32) - bg_rgb, axis=1) > 60]
    if len(ink) < 30:
        return False
    hsv = cv2.cvtColor(ink.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2HSV).reshape(-1, 3)
    vivid = hsv[(hsv[:, 1] > 80) & (hsv[:, 2] > 60)]
    if len(vivid) < 0.3 * len(ink):
        return False
    hist = np.bincount(vivid[:, 0] // 15, minlength=12) / len(vivid)  # 12 hue bins of 30°
    strong = np.nonzero(hist >= 0.15)[0]
    return any(min(abs(a - b), 12 - abs(a - b)) >= 2 for a in strong for b in strong)


def style_line(img: np.ndarray, line: OcrLine) -> tuple[StyledLine, np.ndarray]:
    """Returns the styled line and its glyph mask (in line-box coordinates)."""
    line = _strip_pill_border(line)
    line = _trim_glued_stars(img, line)
    text_rgb, bg_rgb, noise, alpha, mask, size = _measure(img, line)
    if _leading_icon(line, mask, size):  # leave the icon in the picture, re-measure the text alone
        line = _drop_first_word(line)
        text_rgb, bg_rgb, noise, alpha, mask, size = _measure(img, line)

    cut = _trim_icons(mask, line, size)
    if cut:
        line = line.model_copy(deep=True)
        line.box = Box(x=line.box.x + cut, y=line.box.y, w=line.box.w - cut, h=line.box.h)
        line.words[0].box = Box(x=line.box.x, y=line.words[0].box.y,
                                w=max(1, line.words[0].box.x2 - line.box.x), h=line.words[0].box.h)
        alpha, mask = alpha[:, cut:], mask[:, cut:]
        size = _font_size(mask, line.text, line.box.h)

    styled = StyledLine(**line.model_dump(), color=hex_color(text_rgb), bg_color=hex_color(bg_rgb),
                        bg_noise=round(noise, 1), font_size=round(size, 1),
                        multicolor=noise < 25 and _multicolor(img, line.box, bg_rgb))
    for word in line.words:
        wx0, wx1 = max(0, word.box.x - line.box.x), min(mask.shape[1], word.box.x2 - line.box.x)
        wm, wa = mask[:, wx0:wx1], alpha[:, wx0:wx1]
        kind, italic = _typeface(wm, size) if wm.sum() >= 20 else ("sans", False)
        styled.word_kinds.append(kind)
        styled.word_italics.append(italic)
        crop = img[line.box.y:line.box.y2, line.box.x + wx0:line.box.x + wx1]
        if wm.sum() >= 6 and noise < 25 and size >= 28:  # per-word colour/weight: only big text on a plain background
            pixels = crop[wm].astype(np.float32)
            dist = np.linalg.norm(pixels - bg_rgb, axis=1)
            styled.word_colors.append(hex_color(np.median(pixels[dist >= np.percentile(dist, 70)], axis=0)))
            styled.word_weights.append(_weight(wa, size, kind == "serif"))
        else:
            styled.word_colors.append("")
            styled.word_weights.append(0)
    if styled.word_kinds:  # the line's style = what most of its characters use
        n = [len(w.text) for w in line.words]
        serif = sum(c for c, k in zip(n, styled.word_kinds) if k == "serif")
        styled.font_kind = "serif" if serif > sum(n) / 2 else "sans"
        styled.italic = sum(c for c, i in zip(n, styled.word_italics) if i) > sum(n) / 2
    styled.weight = _weight(alpha, size, styled.font_kind == "serif")
    styled.word_colors = [c or styled.color for c in styled.word_colors]
    styled.word_weights = [w or styled.weight for w in styled.word_weights]
    return styled, mask


def _rgb(hex_: str) -> np.ndarray:
    return np.array([int(hex_[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def paint_erase_mask(full: np.ndarray, img: np.ndarray, line: StyledLine) -> None:
    """Mark everything that has to go for this line: glyphs plus their anti-aliased edges,
    and marks just outside the OCR box (i-dots, accents, asterisks, descenders)."""
    H, W = img.shape[:2]
    b, fs = line.box, max(line.font_size, line.box.h)
    pad_y, pad_x = int(fs * 0.35), int(fs * 0.12)
    x0, y0, x1, y1 = max(0, b.x - pad_x), max(0, b.y - pad_y), min(W, b.x2 + pad_x), min(H, b.y2 + pad_y)
    region = Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)
    text, bg = _rgb(line.color), _rgb(line.bg_color)
    crop = img[y0:y1, x0:x1].astype(np.float32)
    axis = text - bg
    norm = float(axis @ axis)
    if norm < 400:
        mask = np.zeros(crop.shape[:2], np.uint8)
        mask[b.y - y0:b.y2 - y0, b.x - x0:b.x2 - x0] = 1
    else:
        mask = ((((crop - bg) @ axis) / norm) > 0.22).astype(np.uint8)
        # Keep blobs that touch the OCR box, or small marks right next to it
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        core = np.zeros_like(mask, bool)
        core[b.y - y0:b.y2 - y0, b.x - x0:b.x2 - x0] = True
        keep = np.zeros(n, bool)
        for i in range(1, n):
            comp = labels == i
            small = stats[i, cv2.CC_STAT_HEIGHT] < fs * 0.45 and stats[i, cv2.CC_STAT_WIDTH] < fs * 0.6
            keep[i] = bool((comp & core).any()) or small
        mask = keep[labels].astype(np.uint8)
    k = max(3, int(fs * 0.08)) | 1
    mask = cv2.dilate(mask, np.ones((k, k), np.uint8))
    full[region.y:region.y2, region.x:region.x2][mask > 0] = 255
