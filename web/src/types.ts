// Mirrors src/img_to_figma/schemas.py

export type Align = "LEFT" | "CENTER" | "RIGHT";

export interface StyleRange {
  start: number;
  end: number;
  color: string;
  font_family: string;
  font_style: string;
}

export interface Layer {
  id: string;
  name: string;
  role: string;
  type: "image" | "text" | "button" | "pill" | "rect";
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
  font_family: string;
  font_style: string;
  font_size: number;
  line_height: number;
  color: string;
  fill: string;
  corner_radius: number;
  padding_x: number;
  align: Align;
  letter_spacing: number;
  fit_w?: number; // width measured in the source image (0/missing = use w)
  ranges: StyleRange[];
  src: string;
  stroke?: string;       // border colour ("" = none)
  stroke_width?: number;
  icon_src?: string;     // button with an icon next to its label
  icon_w?: number;
  icon_h?: number;
  icon_side?: "left" | "right";
  gap?: number;
  visible: boolean;
  locked: boolean;
}

export interface Doc {
  job_id: string;
  source_name: string;
  width: number;
  height: number;
  layers: Layer[];
  fonts: Record<string, string>;
  files: Record<string, string>;
  timings: Record<string, number>;
  warnings: string[];
}

export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "error";
  name: string;
  log: string[];
  document: Doc | null;
  error: string;
}

export interface JobSummary {
  job_id: string;
  name: string;
  width: number;
  height: number;
  layers: number;
  thumb: string;
}

export interface Options {
  cutout_subject: boolean;
  use_llm: boolean;
  scale: number;
  sans_font: string;
  serif_font: string;
  script_font: string;
}
