# Image to Figma

Turns an ad creative or a screenshot into an **editable Figma frame**: real text layers, auto-layout
buttons, the main subject on its own layer, and a cleaned background with the original text removed.
Everything runs on this Mac at no cost.

| Stage | What it does | Engine |
|---|---|---|
| `ocr` | Reads every text line and word, with exact boxes | Apple Vision (built into macOS) |
| `text_style` | Text colour, font size, weight; trims icons (✓ • →) out of text boxes | OpenCV / NumPy |
| `grouping` | Merges lines into blocks, works out alignment and line height | rules |
| `shapes` | Finds solid buttons / pills / badges behind text | OpenCV flood fill |
| `roles` | Headline / CTA / disclaimer…, serif vs sans, emphasised words; drops text that is part of the photo | Qwen3-VL 8B (Ollama) |
| `inpaint` | Erases the editable text and shapes from the picture | OpenCV Telea on smooth areas, LaMa on photos |
| `segment` | Cuts the main subject onto its own transparent layer | rembg (ISNet) |
| `compose` | Writes `document.json` (layer list) + debug overlay | — |

## Setup (once)

```bash
uv sync                          # Python 3.12 env
uv run img-to-figma setup        # downloads LaMa (~200 MB) and rembg (~180 MB)
ollama pull qwen3-vl:8b-instruct # optional: AI labels (Ollama app must be running)
cd web && npm install            # web UI
```

## Use it

**Web editor** (a Figma-like workspace: convert, then edit the layers):

```bash
uv run img-to-figma serve        # API on http://localhost:8001
cd web && npm run dev            # UI on http://localhost:5173
```

**Figma**: Figma desktop → Plugins → Development → *Import plugin from manifest…* →
`figma-plugin/manifest.json`. Run **Image to Figma**, drop / paste an image and press
**Convert & import**, or import anything converted (and edited) in the web UI from *Recent conversions*.

**CLI**:

```bash
uv run img-to-figma convert samples/static-91a.png            # → outputs/<job-id>/
uv run img-to-figma convert shot.png --scale 2 --no-cutout    # retina screenshot, keep subject in background
```

## What becomes a layer

| | UI screenshots | Ads / posters |
|---|---|---|
| Text | editable text (mixed styles kept) | editable text |
| Buttons | auto-layout buttons, icon inside (`+ Add`), border kept | solid buttons / pills |
| Cards, panels, inputs | rectangles with fill, radius and border | — |
| Photos, product shots | image layers (rounded corners kept) | image layers when the ad has flat areas (before/after panels, product shots) |
| Icons, logos, dividers, graphics | transparent PNG layers, "un-blended" so they sit on any colour | stay in the picture |
| Person / product | — | cut-out layer (rembg) when the picture is one photo |
| Background | page colour where things were (shadows of cards removed too) | LaMa / flat fill; a 3% margin around cut-outs so hair and shadows don't smear |

## Workspace

Open http://localhost:5173, drop or paste (⌘V) an image anywhere, and the result opens as a frame on an
infinite canvas. Edits save automatically, and the Figma plugin imports the saved version.

| | |
|---|---|
| Move / pan / zoom | drag layers (snaps to edges and centres, hold ⌘ to skip); scroll or hold Space to pan; ⌘-scroll or pinch to zoom; ⇧1 fit, ⌘0 100% |
| Select | click, ⇧-click to add, drag on empty canvas for a marquee, ⌘A all |
| Edit text | double-click (or Enter); Escape to finish. Mixed styles (italic serif words…) survive edits |
| Resize | drag handles (⇧ keeps ratio); text corners scale the font |
| Tools | V move, H hand, R rectangle, T text |
| Arrange | arrows nudge (⇧ ×10), ⌘D duplicate, ⌫ delete, ⌘] / ⌘[ forward / back, drag in the Layers list |
| History | ⌘Z / ⇧⌘Z |
| Check | **Original** slider lays the source image on top; **Detected** shows what was found |
| Fonts | with nothing selected, *Fonts in this design* replaces a font everywhere and keeps each text at its original width |

## Output (`outputs/<job-id>/`)

| File | |
|---|---|
| `document.json` | Layers (position, text, font, colour, ranges, buttons) — what the plugin builds |
| `background.png` | Picture with editable text / shapes / subject removed |
| `subject.png` | Cut-out subject (if any) |
| `overlay.png` | Debug view: red = editable text, grey = left in the picture, blue = button, green = subject |
| `mask.png` | What was erased |
| `blocks.json` | Raw detection data, for debugging |

The Figma frame also contains a hidden, locked **Original (reference)** layer: switch it on to check alignment.

## Tips

- **Fonts**: set the sans / serif families to your brand fonts (e.g. Poppins). The plugin picks the
  closest installed style; a missing family falls back to Inter and is reported.
- **Match widths** (plugin, on by default; always on in the web preview): font size is estimated from glyph
  height, which differs between typefaces, so each text block is rescaled to the exact width it had in the
  original once the real font is known.
- **Speed** on an M-series Mac with 16 GB: UI screenshots ~5–20 s; ads ~15–30 s without AI labels,
  ~1 min with them (most of it is Qwen3-VL loading and answering). Serif / italic / weight are measured
  from pixels either way.
- **Retina screenshots**: use scale 2× so sizes match the real design.
- **UI screenshots** are recognised automatically (rendered in flat colour, or many separate text
  blocks). Then: no subject cut-out (cards/avatars stay in place), uncertain OCR such as usernames stays
  editable, layers are named by their text (no AI role labels), icons that OCR reads as letters (magnifier → "Q", star → "#") are left in the picture, and
  only clean buttons (one label, no icon inside) become auto-layout frames; inputs, cards, tables and
  icon buttons stay in the background with just their text lifted. Photos inside the UI (product
  shots, thumbnails, hero images) are found by their non-flat pixels, and text printed on them
  (packaging, labels) stays in the picture. Rating stars stay in the picture; the score and count
  beside them ("4.7", "(98)") become separate text layers.
- **Known limits**: OCR misreads stay as typed (e.g. "₹" read as "E", a missing last letter); link
  underlines stay in the background; text-field placeholders become plain text layers; a headline
  overlaid on a photo inside a UI (e.g. a storefront hero in an admin preview) stays in the picture;
  in light-mode UIs, a product photo whose white background exactly matches the page may only be
  partly detected.
- **Without Ollama**: untick *AI labels*; roles come from size/position rules (packaging text may then
  be treated as editable if it is large).
- Settings live in `src/img_to_figma/config.py`, overridable with `I2F_<NAME>` environment variables.

## Layout

```
src/img_to_figma/
  pipeline.py          stages in order, writes the output folder
  server.py            FastAPI for the web UI + plugin (port 8001)
  schemas.py           Layer / Document shapes (same field names as Creative Studio)
  stages/              text_style → grouping → shapes → roles → segment → compose
  engines/             vision_ocr (Apple Vision), lama (inpainting), llm (Ollama)
figma-plugin/          manifest.json, code.js (builds the frame), ui.html
web/                   React + Vite UI
samples/               test creatives
```
