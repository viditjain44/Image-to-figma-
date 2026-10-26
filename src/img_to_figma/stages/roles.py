"""Name each text block (headline, CTA, …) and drop text that is part of the photo.

Qwen3-VL looks at the image with numbered boxes drawn on it and answers in a tiny
plain-text format (JSON is several times slower to generate on a laptop). Typefaces
are measured from pixels in text_style, not asked. Without the LLM, size/position
rules name the blocks.
"""
import logging
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image

from ..engines import llm
from ..schemas import Shape, TextBlock

log = logging.getLogger(__name__)

TextRole = Literal["eyebrow", "headline", "subtext", "body", "cta", "offer_badge", "callout", "stats_bar",
                   "disclaimer", "logo", "part_of_photo"]
ROLES = set(TextRole.__args__)  # type: ignore[attr-defined]

PROMPT = """This is a marketing creative or UI screenshot. Text blocks have numbered red boxes:
{blocks}

For each block write one line "<id> <role>" and nothing else. Roles:
headline = the largest, main message
eyebrow = small label/kicker above or near the headline
subtext = supporting line under the headline
body = paragraph copy
cta = text on a button
offer_badge = discount or offer sticker
callout = benefit pill, stat or list item
stats_bar = row of proof points
disclaimer = fine print
logo = a brand wordmark
part_of_photo = text printed on a product, package, sign or screen inside the photo (not overlay text)

Example:
0 headline
1 subtext
2 part_of_photo"""


def _describe(blocks: list[TextBlock], shapes: list[Shape]) -> str:
    in_shape = {i for s in shapes for i in s.block_ids}
    return "\n".join(f"{b.id}: {b.text[:80]!r} (~{b.font_size:.0f}px{', on a button' if b.id in in_shape else ''})"
                     for b in blocks)


def parse(answer: str) -> dict[int, str]:
    """Reads '<id> <role>' lines; ignores anything malformed."""
    out: dict[int, str] = {}
    for raw in answer.splitlines():
        parts = raw.replace(":", " ").replace("-", " ").split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1] in ROLES:
            out[int(parts[0])] = parts[1]
    return out


def _heuristic(blocks: list[TextBlock], shapes: list[Shape]) -> None:
    in_shape = {i for s in shapes for i in s.block_ids}
    live = [b for b in blocks if not b.keep_in_background]
    biggest = max((b.font_size for b in live), default=0)
    for b in live:
        if b.id in in_shape:
            b.role = "cta" if len(b.text) <= 24 else "callout"
        elif b.font_size >= biggest * 0.85:
            b.role = "headline"
        elif b.font_size < biggest * 0.25:
            b.role = "disclaimer"
        else:
            b.role = "subtext"


def _near_photo_text(blocks: list[TextBlock]) -> None:
    """Text sitting among text that stays in the picture (packaging copy) stays there too."""
    live = [b for b in blocks if not b.keep_in_background]
    if len(live) < 2:
        return
    sizes = sorted(b.font_size for b in live)
    median = sizes[len(sizes) // 2]
    changed = True
    while changed:  # repeat: a label marked now can pull in its neighbours
        changed = False
        kept = [b for b in blocks if b.keep_in_background]
        for b in blocks:
            if b.keep_in_background:
                continue
            reach = b.font_size * 1.5
            # stacked on the same pack (x ranges overlap, close above/below), not merely beside it:
            # "TRY RISK FREE" next to a row of bottles is overlay copy
            near = sum(min(k.box.x2, b.box.x2) > max(k.box.x, b.box.x)
                       and abs(k.box.cy - b.box.cy) < (k.box.h + b.box.h) / 2 + reach for k in kept)
            if near >= 2 or (near == 1 and b.font_size < median * 0.75):
                b.keep_in_background = changed = True


def _small_copy(path: Path, max_side: int = 768) -> Path:
    im = Image.open(path)
    im.thumbnail((max_side, max_side))
    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    im.convert("RGB").save(tmp, quality=90)
    return tmp


def label(blocks: list[TextBlock], shapes: list[Shape], overlay: Path, use_llm: bool) -> str:
    """Fills in roles. Returns '' or a warning."""
    try:
        return _label(blocks, shapes, overlay, use_llm)
    finally:
        _near_photo_text(blocks)


def _label(blocks: list[TextBlock], shapes: list[Shape], overlay: Path, use_llm: bool) -> str:
    _heuristic(blocks, shapes)
    live = [b for b in blocks if not b.keep_in_background]
    if not use_llm or not live:
        return ""
    image = _small_copy(overlay)
    try:
        answer = llm.ask_text(PROMPT.format(blocks=_describe(live, shapes)), images=[image],
                              max_tokens=40 + 8 * len(live))
    except Exception as e:
        log.warning("LLM labelling failed, using rules: %s", e)
        return f"AI labelling skipped ({e.__class__.__name__}); used size/position rules."
    finally:
        image.unlink(missing_ok=True)
    roles = parse(answer)
    if not roles:
        log.warning("Unreadable LLM answer: %r", answer[:300])
        return "AI labelling gave an unreadable answer; used size/position rules."
    by_id = {b.id: b for b in live}
    sizes = sorted(b.font_size for b in live)
    median = sizes[len(sizes) // 2]
    for bid, role in roles.items():
        b = by_id.get(bid)
        if b is None:
            continue
        if role in ("logo", "part_of_photo"):
            # Trust "this is in the photo" only for text on a busy background or small text;
            # a sizeable line on a flat colour is overlay copy, whatever the model says.
            on_photo = max(l.bg_noise for l in b.lines) >= 12
            if on_photo or b.font_size < median * 0.6:
                b.keep_in_background = True
        else:
            b.role = role  # type: ignore[assignment]
    return ""
