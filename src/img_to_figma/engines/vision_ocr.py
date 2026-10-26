"""Text recognition with Apple's Vision framework (built into macOS, runs on the Neural Engine).

Returns one `OcrLine` per text line, with a pixel box for the line and for each word.
"""
import re
from pathlib import Path

import Vision
from Foundation import NSURL

from .. import config
from ..schemas import Box, OcrLine, Word


def _to_px(bb, width: int, height: int) -> Box:
    """Vision boxes are normalised with the origin bottom-left."""
    (x, y), (w, h) = bb.origin, bb.size
    return Box(x=round(x * width), y=round((1 - y - h) * height), w=max(1, round(w * width)), h=max(1, round(h * height)))


def _utf16(s: str, i: int) -> int:
    return len(s[:i].encode("utf-16-le")) // 2


# Rating stars come back as asterisks, often glued to the score: "4.7*****", "3 * **** (1.3K)"
STARS = "*★☆✩✪⭐✦"
_STAR_RUN = re.compile(f"[{re.escape(STARS)}]{{2,}}")


def _tokens(text: str):
    """(start, end) of each word, with runs of 2+ stars split off as their own token.
    A single glued asterisk stays part of its word ("*Does", "straightener*")."""
    for m in re.finditer(r"\S+", text):
        pos = m.start()
        for run in _STAR_RUN.finditer(m.group()):
            a, b = m.start() + run.start(), m.start() + run.end()
            if a > pos:
                yield pos, a
            yield a, b
            pos = b
        if pos < m.end():
            yield pos, m.end()


def _is_stars(token: str) -> bool:
    return all(c in STARS for c in token)


def _split_at_stars(line: OcrLine) -> list[OcrLine]:
    """Stars are artwork: cut the line around them so they stay in the picture
    and the score / count on either side stay editable text."""
    if not any(_is_stars(w.text) for w in line.words):
        return [line]
    out, run = [], []
    for w in line.words + [None]:
        if w is not None and not _is_stars(w.text):
            run.append(w)
            continue
        if run:
            glued = w is not None and run[-1].start + len(run[-1].text) == w.start
            start, end = run[0].start, run[-1].start + len(run[-1].text)
            x0, x1 = min(r.box.x for r in run), max(r.box.x2 for r in run)
            out.append(OcrLine(
                text=line.text[start:end], confidence=line.confidence,
                box=Box(x=x0, y=line.box.y, w=max(1, x1 - x0), h=line.box.h),
                words=[r.model_copy(update={"start": r.start - start}) for r in run], glued_stars=glued))
            run = []
    return out


def recognize(path: Path, width: int, height: int, languages: list[str] | None = None) -> list[OcrLine]:
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    if languages:
        request.setRecognitionLanguages_(languages)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision text recognition failed: {error}")

    lines: list[OcrLine] = []
    for obs in request.results() or []:
        candidate = obs.topCandidates_(1)[0]
        text, confidence = str(candidate.string()), float(candidate.confidence())
        if not text.strip() or confidence < config.MIN_OCR_CONFIDENCE:
            continue
        words = []
        for a, b in _tokens(text):
            start, end = _utf16(text, a), _utf16(text, b)
            rect, _ = candidate.boundingBoxForRange_error_((start, end - start), None)
            if rect is not None:
                words.append(Word(text=text[a:b], box=_to_px(rect.boundingBox(), width, height), start=a))
        line = OcrLine(text=text, box=_to_px(obs.boundingBox(), width, height), confidence=confidence, words=words)
        lines.extend(_split_at_stars(line))
    lines.sort(key=lambda l: (l.box.y, l.box.x))
    return lines
