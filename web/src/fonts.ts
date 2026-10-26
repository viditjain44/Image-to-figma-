import type { CSSProperties } from "react";

// Font helpers: turn Figma-style names ("SemiBold Italic") into CSS, and load Google Fonts on demand.

const WEIGHTS: [string, number][] = [
  ["extralight", 200], ["ultralight", 200], ["thin", 100], ["light", 300], ["semibold", 600], ["demibold", 600],
  ["extrabold", 800], ["ultrabold", 800], ["bold", 700], ["medium", 500], ["black", 900], ["heavy", 900],
];

export function parseStyle(style: string): { weight: number; italic: boolean } {
  const s = (style || "").toLowerCase().replace(/[\s_-]/g, "");
  const hit = WEIGHTS.find(([k]) => s.includes(k));
  return { weight: hit ? hit[1] : 400, italic: /italic|oblique/.test(s) };
}

export const STYLE_OPTIONS = [
  "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black",
  "Light Italic", "Italic", "Medium Italic", "SemiBold Italic", "Bold Italic",
];

const loaded = new Map<string, Promise<void>>();

/** Adds a Google Fonts stylesheet for the family (all weights, both styles). Unknown families just fall back.
 *  Resolves once the stylesheets have arrived (or failed). */
export function ensureFont(family: string): Promise<void> {
  if (!family) return Promise.resolve();
  const known = loaded.get(family);
  if (known) return known;
  // Google rejects a request naming a weight the family lacks, so ask a few ways; failures are harmless.
  const name = encodeURIComponent(family).replace(/%20/g, "+");
  const variants = [
    ":ital,wght@0,100..900;1,100..900",                                   // variable fonts
    ":ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400;1,500;1,600;1,700", // static, full set (Poppins…)
    ":ital,wght@0,400;0,700;1,400;1,700",                                 // static, basic set
  ];
  const done = Promise.all(variants.map((v) => new Promise<void>((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?family=${name}${v}&display=swap`;
    link.onload = link.onerror = () => resolve();
    document.head.appendChild(link);
  }))).then(() => undefined);
  loaded.set(family, done);
  return done;
}

/** Waits until every family/style a set of layers uses is downloaded, so measurements are real. */
export async function loadFontsFor(specs: { family: string; style: string }[]): Promise<void> {
  await Promise.all([...new Set(specs.map((s) => s.family))].map(ensureFont));
  await Promise.all(specs.map(({ family, style }) => {
    const { weight, italic } = parseStyle(style);
    return document.fonts.load(`${italic ? "italic " : ""}${weight} 32px "${family}"`).catch(() => []);
  }));
}

export function fontCss(family: string, style: string): CSSProperties {
  const { weight, italic } = parseStyle(style);
  return {
    fontFamily: `"${family}", Inter, system-ui, sans-serif`,
    fontWeight: weight,
    fontStyle: italic ? "italic" : "normal",
  };
}
