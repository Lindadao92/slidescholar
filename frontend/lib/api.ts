const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ??
  `${process.env.NEXT_PUBLIC_API_URL}";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface ParsedPaper {
  paper_id: string;
  title: string;
  authors: string;
  abstract: string;
  sections: { name: string; text: string }[];
  figures: { path: string; page: number; caption: string }[];
  num_pages: number;
  num_figures: number;
}

export interface GenerateConfig {
  paper_id: string;
  talk_length: "lightning" | "conference" | "seminar";
  template_style: "minimal" | "academic" | "dark";
  include_speaker_notes: boolean;
  include_backup_slides: boolean;
}

export interface SlideData {
  slide_number: number;
  title: string;
  content_type: string;
  bullet_points: string[];
  figure_reference: string | null;
  speaking_time_seconds: number;
  speaker_notes: string;
  transition: string;
}

export interface GenerateResult {
  download_url: string;
  slide_plan: {
    talk_title: string;
    talk_length_minutes: number;
    total_slides: number;
    slides: SlideData[];
  };
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message =
      body?.detail ??
      body?.message ??
      `Request failed with status ${res.status}`;
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ */
/*  API functions                                                      */
/* ------------------------------------------------------------------ */

export async function parsePaper(file: File): Promise<ParsedPaper> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/api/parse`, {
    method: "POST",
    body: form,
  });

  return handleResponse<ParsedPaper>(res);
}

export async function parseArxivUrl(url: string): Promise<ParsedPaper> {
  const res = await fetch(`${API_BASE}/api/parse-arxiv`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });

  return handleResponse<ParsedPaper>(res);
}

export async function generateSlides(
  config: GenerateConfig
): Promise<GenerateResult> {
  const res = await fetch(`${API_BASE}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

  return handleResponse<GenerateResult>(res);
}

export function getDownloadUrl(fileId: string): string {
  return `${API_BASE}/api/download/${encodeURIComponent(fileId)}`;
}
