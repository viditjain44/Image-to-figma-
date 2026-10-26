"""Data shapes passed between stages, saved as document.json and sent to Figma.

`Layer` keeps the same field names as Creative Studio's layers, so either
plugin can build the other's output.
"""
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["background", "product", "logo", "eyebrow", "headline", "subtext", "cta", "offer_badge",
               "callout", "stats_bar", "body", "disclaimer", "shape", "card", "photo", "icon"]
FontKind = Literal["sans", "serif", "script"]


class Box(BaseModel):
    """Pixel rectangle, origin top-left."""
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def union(self, o: "Box") -> "Box":
        x, y = min(self.x, o.x), min(self.y, o.y)
        return Box(x=x, y=y, w=max(self.x2, o.x2) - x, h=max(self.y2, o.y2) - y)

    def contains(self, o: "Box", slack: int = 0) -> bool:
        return (o.x >= self.x - slack and o.y >= self.y - slack
                and o.x2 <= self.x2 + slack and o.y2 <= self.y2 + slack)


class Word(BaseModel):
    text: str
    box: Box
    start: int  # character offset inside its line


class OcrLine(BaseModel):
    """One line of text as Apple Vision reads it."""
    text: str
    box: Box
    confidence: float
    words: list[Word] = Field(default_factory=list)
    glued_stars: bool = False    # stars were glued to the end ("4.7*****"): the box still covers them


class StyledLine(OcrLine):
    index: int = -1              # position in the OCR result
    color: str = "#000000"
    bg_color: str = "#FFFFFF"
    bg_noise: float = 0          # how busy the background around the line is (0 = flat colour)
    multicolor: bool = False     # glyphs in several colours: a logo / artwork
    font_size: float = 0
    weight: int = 400            # CSS-style weight: 300..900
    word_colors: list[str] = Field(default_factory=list)
    word_weights: list[int] = Field(default_factory=list)
    word_kinds: list[FontKind] = Field(default_factory=list)   # serif vs sans, from stroke contrast
    word_italics: list[bool] = Field(default_factory=list)     # from glyph slant
    font_kind: FontKind = "sans"                               # majority over the line's characters
    italic: bool = False


class TextBlock(BaseModel):
    """Lines that belong together (one paragraph / one heading)."""
    id: int
    lines: list[StyledLine]
    box: Box
    align: Literal["LEFT", "CENTER", "RIGHT"] = "LEFT"
    line_height: float = 0       # px between baselines; 0 = single line
    role: Role = "body"
    keep_in_background: bool = False  # e.g. text that is part of a photo or a logo

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    @property
    def font_size(self) -> float:
        sizes = sorted(l.font_size for l in self.lines)
        return sizes[len(sizes) // 2]

    @property
    def font_kind(self) -> FontKind:
        """The typeface kind covering most characters."""
        counts: dict[str, int] = {}
        for l in self.lines:
            counts[l.font_kind] = counts.get(l.font_kind, 0) + len(l.text)
        return max(counts, key=counts.get)  # type: ignore[return-value]

    @property
    def italic(self) -> bool:
        kind = self.font_kind
        same = [l for l in self.lines if l.font_kind == kind]
        return sum(len(l.text) for l in same if l.italic) > sum(len(l.text) for l in same) / 2


class Shape(BaseModel):
    """A solid-colour rounded rectangle found behind text (button, pill, badge, bar)."""
    box: Box
    fill: str
    corner_radius: int
    block_ids: list[int]


class StyleRange(BaseModel):
    start: int
    end: int
    color: str = ""
    font_family: str = ""
    font_style: str = ""


class Layer(BaseModel):
    """One Figma layer. Coordinates are pixels in the source image."""
    id: str
    name: str
    role: Role
    type: Literal["image", "text", "button", "pill", "rect"]
    x: int
    y: int
    w: int
    h: int
    text: str = ""
    font_family: str = ""
    font_style: str = ""
    font_size: int = 0
    line_height: int = 0         # px; 0 = auto
    color: str = ""
    fill: str = ""
    corner_radius: int = 0
    padding_x: int = 0
    align: Literal["LEFT", "CENTER", "RIGHT"] = "LEFT"
    letter_spacing: float = 0    # percent of font size, like Figma
    fit_w: int = 0               # text width measured in the source image; editors size text to it (0 = use w)
    ranges: list[StyleRange] = Field(default_factory=list)
    src: str = ""                # image path relative to the outputs folder
    stroke: str = ""             # border colour of a rect / button ("" = none)
    stroke_width: int = 0
    icon_src: str = ""           # button with an icon: image shown next to the label (inside the auto-layout)
    icon_w: int = 0
    icon_h: int = 0
    icon_side: Literal["left", "right"] = "left"
    gap: int = 0                 # space between icon and label
    visible: bool = True
    locked: bool = False


class Document(BaseModel):
    job_id: str
    source_name: str
    width: int
    height: int
    layers: list[Layer]
    fonts: dict[str, str] = Field(default_factory=dict)  # kind -> family used
    files: dict[str, str] = Field(default_factory=dict)  # original / background / overlay / preview
    timings: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
