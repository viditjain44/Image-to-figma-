// Image to Figma plugin: builds an editable frame from a document.json made by the img-to-figma server.
figma.showUI(__html__, { width: 360, height: 620 });

function rgb(hex) {
  const h = (hex || "#000000").replace("#", "");
  return { r: parseInt(h.slice(0, 2), 16) / 255, g: parseInt(h.slice(2, 4), 16) / 255, b: parseInt(h.slice(4, 6), 16) / 255 };
}
const solid = (hex) => [{ type: "SOLID", color: rgb(hex) }];

// ---------- fonts: pick the closest style the family really has ----------
const WEIGHTS = [["extralight", 200], ["ultralight", 200], ["thin", 100], ["light", 300], ["semibold", 600],
  ["demibold", 600], ["extrabold", 800], ["ultrabold", 800], ["bold", 700], ["medium", 500], ["black", 900],
  ["heavy", 900], ["regular", 400], ["normal", 400], ["book", 400], ["roman", 400]];
function parseStyle(style) {
  const s = (style || "").toLowerCase().replace(/[\s_-]/g, "");
  const hit = WEIGHTS.find(([k]) => s.includes(k));
  return { weight: hit ? hit[1] : 400, italic: /italic|oblique/.test(s) };
}

let fontIndex = null; // family -> [{family, style}]
async function availableFonts() {
  if (!fontIndex) {
    fontIndex = {};
    for (const f of await figma.listAvailableFontsAsync()) (fontIndex[f.fontName.family] = fontIndex[f.fontName.family] || []).push(f.fontName);
  }
  return fontIndex;
}
const loaded = new Map();
async function resolveFont(family, style) {
  const key = `${family}|${style}`;
  if (loaded.has(key)) return loaded.get(key);
  const fonts = await availableFonts();
  const options = fonts[family] || fonts["Inter"];
  const want = parseStyle(style);
  let best = options[0], bestScore = Infinity;
  for (const f of options) {
    const p = parseStyle(f.style);
    const score = Math.abs(p.weight - want.weight) + (p.italic !== want.italic ? 250 : 0);
    if (score < bestScore) { best = f; bestScore = score; }
  }
  await figma.loadFontAsync(best);
  loaded.set(key, best);
  return best;
}

// ---------- layers ----------
async function makeText(l, withLineHeight) {
  const t = figma.createText();
  t.fontName = await resolveFont(l.font_family, l.font_style);
  t.characters = l.text;
  t.fontSize = l.font_size;
  t.lineHeight = withLineHeight && l.line_height ? { value: l.line_height, unit: "PIXELS" } : { unit: "AUTO" };
  t.fills = solid(l.color);
  t.textAlignHorizontal = l.align;
  if (l.letter_spacing) t.letterSpacing = { value: l.letter_spacing, unit: "PERCENT" };
  for (const r of l.ranges || []) {
    const end = Math.min(r.end, l.text.length);
    if (r.start >= end) continue;
    if (r.font_family || r.font_style)
      t.setRangeFontName(r.start, end, await resolveFont(r.font_family || l.font_family, r.font_style || l.font_style));
    if (r.color) t.setRangeFills(r.start, end, solid(r.color));
  }
  t.textAutoResize = "WIDTH_AND_HEIGHT";
  return t;
}

function addStroke(node, l) {
  if (!l.stroke || !l.stroke_width) return;
  node.strokes = solid(l.stroke);
  node.strokeWeight = l.stroke_width;
  node.strokeAlign = "INSIDE";
}

async function buildLayer(l, images) {
  let node;
  if (l.type === "image") {
    node = figma.createRectangle();
    node.resize(l.w, l.h);
    node.fills = [{ type: "IMAGE", imageHash: figma.createImage(images[l.src]).hash, scaleMode: "FILL" }];
    node.x = l.x; node.y = l.y;
  } else if (l.type === "text") {
    node = await makeText(l, true);
    // Sizes are estimated from glyph height, which varies by font; the measured width is exact.
    // So once the real font is known, scale the size until the block is as wide as the original.
    const ratio = (l.fit_w || l.w) / node.width; // fit_w: width in the original image, kept through edits
    if (fitWidths && (ratio < 0.96 || ratio > 1.04)) {
      const k = Math.min(1.25, Math.max(0.75, ratio));
      node.fontSize = Math.max(1, Math.round(l.font_size * k));
      if (l.line_height) node.lineHeight = { value: Math.round(l.line_height * k), unit: "PIXELS" };
    }
    // Keep the measured anchor: left edge, centre or right edge of the original text
    node.x = l.align === "CENTER" ? l.x + (l.w - node.width) / 2 : l.align === "RIGHT" ? l.x + l.w - node.width : l.x;
    node.y = l.y;
  } else if (l.type === "rect") {
    node = figma.createRectangle();
    node.resize(l.w, l.h);
    node.fills = solid(l.fill);
    node.cornerRadius = l.corner_radius;
    addStroke(node, l);
    node.x = l.x; node.y = l.y;
  } else {
    // button / pill: auto-layout frame, so editing the text keeps it centred
    node = figma.createFrame();
    node.layoutMode = "HORIZONTAL";
    node.primaryAxisAlignItems = "CENTER";
    node.counterAxisAlignItems = "CENTER";
    node.fills = solid(l.fill);
    node.cornerRadius = l.corner_radius;
    addStroke(node, l);
    const label = await makeText(l, false);
    if (l.icon_src && images[l.icon_src]) { // "+ Add Hidden Rate": icon and label share the auto-layout
      const icon = figma.createRectangle();
      icon.name = "Icon";
      icon.resize(Math.max(1, l.icon_w), Math.max(1, l.icon_h));
      icon.fills = [{ type: "IMAGE", imageHash: figma.createImage(images[l.icon_src]).hash, scaleMode: "FIT" }];
      node.itemSpacing = l.gap || 0;
      if (l.icon_side === "right") { node.appendChild(label); node.appendChild(icon); }
      else { node.appendChild(icon); node.appendChild(label); }
    } else {
      node.appendChild(label);
    }
    node.paddingLeft = node.paddingRight = Math.max(4, l.padding_x);
    node.resize(l.w, l.h);
    node.primaryAxisSizingMode = "FIXED";
    node.counterAxisSizingMode = "FIXED";
    node.x = l.x; node.y = l.y;
  }
  node.name = l.name;
  node.visible = l.visible !== false;
  node.locked = !!l.locked;
  return node;
}

let fitWidths = true;

figma.ui.onmessage = async (msg) => {
  if (msg.type !== "import") return;
  const doc = msg.doc;
  fitWidths = msg.fitWidths !== false;
  try {
    const frame = figma.createFrame();
    frame.name = `${doc.source_name} · ${doc.job_id}`;
    frame.resize(doc.width, doc.height);
    frame.clipsContent = true;
    frame.x = Math.round(figma.viewport.center.x - doc.width / 2);
    frame.y = Math.round(figma.viewport.center.y - doc.height / 2);
    const missing = new Set();
    const fonts = await availableFonts();
    for (const l of doc.layers) {
      if (l.font_family && !fonts[l.font_family]) missing.add(l.font_family);
      frame.appendChild(await buildLayer(l, msg.images));
    }
    frame.setPluginData("job_id", doc.job_id);
    figma.currentPage.selection = [frame];
    figma.viewport.scrollAndZoomIntoView([frame]);
    const note = missing.size ? ` (not installed, used Inter: ${[...missing].join(", ")})` : "";
    figma.notify(`Imported ${doc.layers.length} layers${note}`);
    figma.ui.postMessage({ type: "imported", missing: [...missing] });
  } catch (e) {
    figma.notify("Import failed: " + e.message, { error: true });
    figma.ui.postMessage({ type: "error", message: String(e.message || e) });
  }
};
