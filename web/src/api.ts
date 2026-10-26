import type { Doc, Job, JobSummary, Options } from "./types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const fileUrl = (path: string) => `/files/${path}`;

export const api = {
  health: () => call<{ ok: boolean }>("/health"),
  defaults: () => call<Options>("/defaults"),
  jobs: () => call<JobSummary[]>("/jobs"),
  job: (id: string) => call<Job>(`/jobs/${id}`),
  convert: (file: File, options: Options) => {
    const form = new FormData();
    form.append("image", file, file.name);
    form.append("options", JSON.stringify(options));
    return call<{ job_id: string; position_in_queue: number }>("/convert", { method: "POST", body: form });
  },
  save: (doc: Doc) =>
    call<{ ok: boolean }>(`/jobs/${doc.job_id}/document`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(doc),
    }),
};
