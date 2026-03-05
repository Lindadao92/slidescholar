"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface FigureMeta {
  url: string;
  figure_label: string;
  caption: string;
  page: number;
}

interface TableData {
  headers: string[];
  rows: unknown[];
  caption?: string;
}

interface BackendSlide {
  slide_number?: number;
  title: string;
  content_type?: string;
  layout?: string;
  bullet_points?: string[];
  annotations?: string[];
  figure_reference?: string | null;
  table_data?: TableData | null;
  table_headline?: string;
  equation_latex?: string;
  key_number?: string;
  key_number_context?: string;
  context_line?: string;
  speaking_time_seconds?: number;
  speaker_notes?: string;
  transition?: string;
  timing_cue?: string;
  source_section?: string;
  references?: string[];
}

interface Slide {
  title: string;
  subtitle: string;
  authors: string;
  venue: string;
  bullets: string[];
  annotations: string[];
  contextLine: string;
  figureUrl: string | null;
  layout: string;
  contentType: string;
  tableData: TableData | null;
  tableHeadline: string;
  equationLatex: string;
  keyNumber: string;
  keyNumberContext: string;
  speakerNotes: string;
  speakingTime: number;
  transition: string;
  sourceSection: string;
  references: string[];
  isBackup: boolean;
}

interface SlidePlan {
  talk_title?: string;
  talk_subtitle?: string;
  authors?: string;
  venue?: string;
  slides?: BackendSlide[];
  backup_slides?: BackendSlide[];
  [key: string]: unknown;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

/** Slide field (camelCase) -> BackendSlide field (snake_case) */
const FIELD_MAP: Record<string, string> = {
  title: "title",
  bullets: "bullet_points",
  annotations: "annotations",
  contextLine: "context_line",
  tableHeadline: "table_headline",
  tableData: "table_data",
  keyNumber: "key_number",
  keyNumberContext: "key_number_context",
  speakerNotes: "speaker_notes",
  equationLatex: "equation_latex",
};

/** Fields stored at plan level, not slide level */
const PLAN_FIELDS: Record<string, string> = {
  subtitle: "talk_subtitle",
  authors: "authors",
  venue: "venue",
};

function resolveUrl(path: string): string {
  if (path.startsWith("http")) return path;
  return `${API_BASE}${path}`;
}

/**
 * Match figure reference to extracted figure metadata.
 * Mirrors backend _find_figure() strategy:
 *   1. figure_number match (extract N from "Figure N")
 *   2. Page hint match with +-1 tolerance
 *   3. Index fallback (1-based)
 */
function matchFigure(
  ref: string | null | Record<string, unknown>,
  figures: FigureMeta[]
): FigureMeta | null {
  if (!figures.length) return null;
  if (!ref) return null;

  let refStr: string;
  if (typeof ref === "object") {
    const num = (ref as Record<string, unknown>).figure_number ?? "";
    const page = (ref as Record<string, unknown>).page_number ?? (ref as Record<string, unknown>).page ?? "";
    refStr = num ? `Figure ${num} (page ${page})` : "";
  } else {
    refStr = String(ref);
  }
  if (!refStr) return null;

  const numMatch = refStr.match(/Figure\s*(\d+)/i);
  const targetNum = numMatch ? parseInt(numMatch[1]) : null;

  if (targetNum !== null) {
    const byLabel = figures.find((f) => {
      if (!f.figure_label) return false;
      const m = f.figure_label.match(/Figure\s*(\d+)/i);
      return m ? parseInt(m[1]) === targetNum : false;
    });
    if (byLabel) return byLabel;
  }

  const pageMatch = refStr.match(/\(?page\s*(\d+)\)?/i);
  if (pageMatch) {
    const targetPage = parseInt(pageMatch[1]);
    const exact = figures.find((f) => f.page === targetPage);
    if (exact) return exact;
    const near = figures.find((f) => Math.abs(f.page - targetPage) <= 1);
    if (near) return near;
  }

  if (targetNum !== null) {
    const idx = targetNum - 1;
    if (idx >= 0 && idx < figures.length) return figures[idx];
  }

  return null;
}

/** Normalise a table row */
function normaliseRow(row: unknown): { cells: string[]; bold: boolean } {
  if (Array.isArray(row)) return { cells: row.map(String), bold: false };
  if (row && typeof row === "object") {
    const r = row as Record<string, unknown>;
    const cells = Array.isArray(r.cells) ? r.cells.map(String) : [];
    return { cells, bold: !!r.bold };
  }
  return { cells: [], bold: false };
}

function mapSlide(
  b: BackendSlide,
  isBackup: boolean,
  figures: FigureMeta[],
  plan: SlidePlan
): Slide {
  const fig = matchFigure(b.figure_reference as string | Record<string, unknown> | null, figures);
  const isTitle = b.content_type === "title";
  return {
    title: b.title ?? "",
    subtitle: isTitle ? (plan.talk_subtitle ?? "") : "",
    authors: isTitle ? (plan.authors ?? "") : "",
    venue: isTitle ? (plan.venue ?? "") : "",
    bullets: b.bullet_points ?? [],
    annotations: (b.annotations ?? []).map((a) => (typeof a === "string" ? a : String(a))),
    contextLine: b.context_line ?? "",
    figureUrl: fig ? resolveUrl(fig.url) : null,
    layout: b.layout ?? "bullets",
    contentType: b.content_type ?? "content",
    tableData: b.table_data ?? null,
    tableHeadline: b.table_headline ?? "",
    equationLatex: b.equation_latex ?? "",
    keyNumber: b.key_number ?? "",
    keyNumberContext: b.key_number_context ?? "",
    speakerNotes: b.speaker_notes ?? "",
    speakingTime: b.speaking_time_seconds ?? 90,
    transition: b.transition ?? "",
    sourceSection: b.source_section ?? "",
    references: b.references ?? [],
    isBackup,
  };
}

function typeIcon(ct: string): string {
  if (ct === "title") return "\uD83C\uDFAC";
  if (ct.startsWith("backup")) return "\uD83D\uDCCB";
  if (ct === "results" || ct === "analysis") return "\uD83D\uDCCA";
  if (ct === "method") return "\u2699\uFE0F";
  if (ct === "motivation") return "\uD83D\uDCA1";
  if (ct === "conclusion" || ct === "thankyou") return "\uD83C\uDFC1";
  return "\uD83D\uDCC4";
}

function typeLabel(ct: string): string {
  if (ct.startsWith("backup_")) return "Backup";
  return ct.charAt(0).toUpperCase() + ct.slice(1);
}

/* ------------------------------------------------------------------ */
/*  EditableText — self-contained click-to-edit component              */
/* ------------------------------------------------------------------ */

function EditableText({
  value,
  onChange,
  className = "",
  placeholder = "Click to edit...",
  multiline = false,
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
  multiline?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => { setDraft(value); }, [value]);

  if (!editing) {
    return (
      <span
        className={`cursor-text rounded px-1 -mx-1 transition-colors hover:bg-blue-50/80 hover:outline hover:outline-1 hover:outline-blue-200 ${className}`}
        onClick={() => setEditing(true)}
        title="Click to edit"
      >
        {value || <span className="italic text-gray-300">{placeholder}</span>}
      </span>
    );
  }

  const finish = () => {
    setEditing(false);
    if (draft.trim() !== value) onChange(draft.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setDraft(value); setEditing(false); }
    if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      finish();
    }
  };

  const cls = `w-full rounded border border-blue-300 bg-white px-1 -mx-1 focus:outline-none focus:ring-2 focus:ring-blue-400/50 ${className}`;

  return multiline ? (
    <textarea
      autoFocus
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={finish}
      onKeyDown={handleKeyDown}
      rows={Math.max(2, Math.ceil(draft.length / 50))}
      className={`${cls} resize-none`}
    />
  ) : (
    <input
      autoFocus
      type="text"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={finish}
      onKeyDown={handleKeyDown}
      className={cls}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  BulletList — editable bullet points with add/delete                */
/* ------------------------------------------------------------------ */

function BulletList({
  bullets,
  onChange,
  className = "",
}: {
  bullets: string[];
  onChange: (bs: string[]) => void;
  className?: string;
}) {
  if (!bullets.length) {
    return (
      <button
        onClick={() => onChange(["New point"])}
        className={`text-left text-[11px] text-gray-300 hover:text-accent ${className}`}
      >
        + Add bullet point
      </button>
    );
  }

  return (
    <div className={`space-y-0.5 overflow-hidden ${className}`}>
      {bullets.map((b, i) => (
        <div key={i} className="group flex items-start gap-1.5">
          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent/60" />
          <EditableText
            value={b}
            onChange={(v) => {
              const next = [...bullets];
              next[i] = v;
              onChange(next);
            }}
            className="flex-1 text-[11px] leading-snug text-gray-600 sm:text-xs"
          />
          <button
            onClick={() => onChange(bullets.filter((_, j) => j !== i))}
            className="shrink-0 px-0.5 text-[10px] text-red-400 opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100"
            title="Remove"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...bullets, "New point"])}
        className="text-[10px] text-gray-300 hover:text-accent"
      >
        + Add bullet
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  AnnotationList — editable annotations with add/delete              */
/* ------------------------------------------------------------------ */

function AnnotationList({
  annotations,
  onChange,
  className = "",
}: {
  annotations: string[];
  onChange: (anns: string[]) => void;
  className?: string;
}) {
  if (!annotations.length) return null;

  return (
    <div className={`space-y-0.5 ${className}`}>
      {annotations.map((a, i) => (
        <div key={i} className="group flex items-start gap-1">
          <EditableText
            value={a}
            onChange={(v) => {
              const next = [...annotations];
              next[i] = v;
              onChange(next);
            }}
            className="flex-1 text-[10px] leading-snug text-gray-500"
          />
          <button
            onClick={() => onChange(annotations.filter((_, j) => j !== i))}
            className="shrink-0 px-0.5 text-[10px] text-red-400 opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100"
            title="Remove"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...annotations, "New annotation"])}
        className="text-[10px] text-gray-300 hover:text-accent"
      >
        + Add annotation
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  EditableSlideTable — inline editable headers + cells               */
/* ------------------------------------------------------------------ */

function EditableSlideTable({
  tableData,
  onChange,
}: {
  tableData: TableData;
  onChange: (td: TableData) => void;
}) {
  function updateHeader(idx: number, value: string) {
    const newHeaders = [...tableData.headers];
    newHeaders[idx] = value;
    onChange({ ...tableData, headers: newHeaders });
  }

  function updateCell(rowIdx: number, cellIdx: number, value: string) {
    const newRows = tableData.rows.map((rawRow, ri) => {
      if (ri !== rowIdx) return rawRow;
      const row = normaliseRow(rawRow);
      const newCells = [...row.cells];
      newCells[cellIdx] = value;
      return { cells: newCells, bold: row.bold };
    });
    onChange({ ...tableData, rows: newRows });
  }

  return (
    <div className="shrink-0 overflow-hidden rounded-lg border border-gray-200">
      <table className="w-full table-fixed text-[8px]">
        {tableData.headers?.length > 0 && (
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {tableData.headers.map((h, i) => (
                <th key={i} className="px-1 py-0.5 text-left font-semibold">
                  <input
                    type="text"
                    value={h}
                    onChange={(e) => updateHeader(i, e.target.value)}
                    className="w-full rounded bg-transparent px-0.5 font-semibold focus:bg-blue-50 focus:outline-none"
                  />
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {(tableData.rows ?? []).map((rawRow, ri) => {
            const row = normaliseRow(rawRow);
            return (
              <tr key={ri} className="border-b border-gray-50">
                {row.cells.map((cell, ci) => (
                  <td key={ci} className={`px-1 py-0.5 ${row.bold ? "font-semibold" : ""}`}>
                    <input
                      type="text"
                      value={cell}
                      onChange={(e) => updateCell(ri, ci, e.target.value)}
                      className="w-full truncate rounded bg-transparent px-0.5 focus:bg-blue-50 focus:outline-none"
                    />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main preview page                                                  */
/* ------------------------------------------------------------------ */

export default function PreviewPage() {
  const router = useRouter();
  const [slides, setSlides] = useState<Slide[]>([]);
  const [slidePlan, setSlidePlan] = useState<SlidePlan | null>(null);
  const [paperId, setPaperId] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [hasEdits, setHasEdits] = useState(false);
  const [isRebuilding, setIsRebuilding] = useState(false);
  const [draftNotes, setDraftNotes] = useState("");
  const mainCountRef = useRef(0);

  /* ---- Load data ---- */
  useEffect(() => {
    const genRaw = sessionStorage.getItem("generateResult");
    if (!genRaw) { router.replace("/"); return; }
    try {
      const data = JSON.parse(genRaw);
      const plan: SlidePlan = data.slide_plan ?? {};
      setSlidePlan(plan);

      const parseRaw = sessionStorage.getItem("parseResult");
      const parseData = parseRaw ? JSON.parse(parseRaw) : {};
      const figures: FigureMeta[] = parseData.figures ?? [];
      setPaperId(parseData.paper_id ?? null);

      const mainSlides = (plan.slides ?? []).map((s: BackendSlide) =>
        mapSlide(s, false, figures, plan)
      );
      const backupSlides = (plan.backup_slides ?? []).map(
        (s: BackendSlide) => mapSlide(s, true, figures, plan)
      );
      mainCountRef.current = mainSlides.length;
      const all = [...mainSlides, ...backupSlides];
      if (!all.length) { router.replace("/"); return; }
      setSlides(all);
      setDownloadUrl(data.download_url ?? null);
    } catch { router.replace("/"); }
  }, [router]);

  /* ---- Sync speaker notes on selection change ---- */
  useEffect(() => {
    if (slides.length) setDraftNotes(slides[selected].speakerNotes);
  }, [selected, slides]);

  /* ---- Update a single field on a slide ---- */
  const updateSlide = useCallback(
    (index: number, field: string, value: unknown) => {
      setSlides((prev) => {
        const next = [...prev];
        next[index] = { ...next[index], [field]: value };
        return next;
      });

      setSlidePlan((prev) => {
        if (!prev) return prev;
        const updated = { ...prev };

        // Plan-level fields (subtitle, authors, venue on title slide)
        const planField = PLAN_FIELDS[field];
        if (planField) {
          (updated as Record<string, unknown>)[planField] = value;
        } else {
          // Slide-level fields
          const backendField = FIELD_MAP[field] ?? field;
          if (index < mainCountRef.current) {
            updated.slides = [...(prev.slides ?? [])];
            updated.slides[index] = { ...updated.slides[index], [backendField]: value };
          } else {
            const bi = index - mainCountRef.current;
            updated.backup_slides = [...(prev.backup_slides ?? [])];
            updated.backup_slides[bi] = { ...updated.backup_slides[bi], [backendField]: value };
          }
        }

        // Persist to sessionStorage
        try {
          const raw = sessionStorage.getItem("generateResult");
          if (raw) {
            const d = JSON.parse(raw);
            d.slide_plan = updated;
            sessionStorage.setItem("generateResult", JSON.stringify(d));
          }
        } catch { /* ignore */ }

        return updated;
      });

      setHasEdits(true);
    },
    []
  );

  /* ---- Delete slide ---- */
  const deleteSlide = useCallback(() => {
    if (slides.length <= 1) return;
    const idx = selected;
    const currentMainCount = mainCountRef.current;
    const isMain = idx < currentMainCount;
    const backupIdx = idx - currentMainCount;

    setSlides((prev) => prev.filter((_, i) => i !== idx));
    setSlidePlan((prev) => {
      if (!prev) return prev;
      const updated = { ...prev };
      if (isMain) {
        updated.slides = (prev.slides ?? []).filter((_, i) => i !== idx);
      } else {
        updated.backup_slides = (prev.backup_slides ?? []).filter((_, i) => i !== backupIdx);
      }
      return updated;
    });

    if (isMain) mainCountRef.current -= 1;
    setSelected((p) => Math.min(p, slides.length - 2));
    setHasEdits(true);
  }, [selected, slides.length]);

  /* ---- Download (original file if no edits, or rebuild) ---- */
  const handleDownload = useCallback(async () => {
    if (!hasEdits && downloadUrl) {
      try {
        const res = await fetch(resolveUrl(downloadUrl));
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "slidescholar_presentation.pptx";
        a.click();
        URL.revokeObjectURL(a.href);
      } catch { alert("Download failed. Please try again."); }
      return;
    }

    if (!slidePlan || !paperId) {
      alert("Session expired. Please go back and regenerate.");
      return;
    }
    setIsRebuilding(true);
    try {
      const res = await fetch(`${API_BASE}/api/rebuild`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slide_plan: slidePlan, paper_id: paperId }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Rebuild failed" }));
        throw new Error(err.detail || "Rebuild failed");
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "slidescholar_presentation.pptx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      alert(`Download failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setIsRebuilding(false);
    }
  }, [hasEdits, downloadUrl, slidePlan, paperId]);

  // Ref for keyboard handler to access latest handleDownload
  const downloadRef = useRef(handleDownload);
  useEffect(() => { downloadRef.current = handleDownload; }, [handleDownload]);

  /* ---- Keyboard: arrows + Ctrl/Cmd+S ---- */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      const isInput = tag === "INPUT" || tag === "TEXTAREA";
      if (!isInput) {
        if (e.key === "ArrowLeft") { e.preventDefault(); setSelected((p) => Math.max(0, p - 1)); }
        if (e.key === "ArrowRight") { e.preventDefault(); setSelected((p) => Math.min(slides.length - 1, p + 1)); }
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        downloadRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [slides.length]);

  if (!slides.length) return null;

  const current = slides[selected];
  const mainCount = slides.filter((s) => !s.isBackup).length;
  const backupCount = slides.filter((s) => s.isBackup).length;
  const firstBackupIdx = slides.findIndex((s) => s.isBackup);

  const hasFigure = !!current.figureUrl;
  const hasTable = !!current.tableData;
  const hasEquation = !!current.equationLatex;
  const hasKeyNumber = !!current.keyNumber;
  const isTitleSlide = current.contentType === "title";
  const isThankYou = current.contentType === "thankyou";

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* ---- Top bar ---- */}
      <nav className="flex shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 py-2.5">
        <button
          onClick={() => router.push("/configure")}
          className="text-sm font-medium text-gray-500 hover:text-foreground"
        >
          &larr; Back to Configure
        </button>
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold tracking-tight">
            Slide<span className="text-accent">Scholar</span>
          </span>
          {hasEdits && (
            <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] font-semibold text-yellow-700">
              Edited
            </span>
          )}
        </div>
        <button
          onClick={handleDownload}
          disabled={isRebuilding}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {isRebuilding ? "Building..." : "\u2193 Download .pptx"}
        </button>
      </nav>

      {/* ---- 3-panel body ---- */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* == Left: slide list == */}
        <aside className="flex shrink-0 gap-2 overflow-x-auto border-b border-gray-200 bg-gray-50 px-3 py-3 lg:w-56 lg:flex-col lg:overflow-y-auto lg:overflow-x-hidden lg:border-b-0 lg:border-r lg:py-4">
          {slides.map((s, i) => (
            <div key={i}>
              {i === firstBackupIdx && firstBackupIdx > 0 && (
                <div className="mb-2 mt-3 hidden border-t border-gray-200 pt-3 lg:block">
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                    Backup ({backupCount})
                  </span>
                </div>
              )}
              <button
                onClick={() => setSelected(i)}
                className={`flex w-40 shrink-0 items-start gap-2 rounded-lg border-2 px-3 py-2 text-left transition-colors lg:w-full ${
                  i === selected
                    ? "border-accent bg-blue-50"
                    : s.isBackup
                      ? "border-transparent bg-amber-50/50 hover:border-gray-200"
                      : "border-transparent bg-white hover:border-gray-200"
                }`}
              >
                <span className="mt-0.5 text-xs">{typeIcon(s.contentType)}</span>
                <span className="min-w-0 flex-1">
                  <span className="text-[11px] font-semibold text-gray-400">
                    {s.isBackup ? "B" : i + 1}
                  </span>
                  <span className="block truncate text-xs font-medium text-foreground">
                    {s.title}
                  </span>
                </span>
              </button>
            </div>
          ))}
        </aside>

        {/* == Center: slide preview == */}
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-gray-100 px-4 py-6 lg:px-8">
          <div className="mx-auto w-full max-w-2xl">
            {/* 16:9 slide card */}
            <div className="relative aspect-video overflow-hidden rounded-xl border border-gray-200 bg-white shadow-md">
              <div className="flex h-full flex-col overflow-hidden p-5 sm:p-8">
                {/* Badges */}
                <span className="absolute right-3 top-2.5 text-[10px] font-semibold text-gray-300">
                  {selected + 1} / {slides.length}
                </span>
                {current.isBackup && (
                  <span className="absolute left-3 top-2.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-600">
                    BACKUP
                  </span>
                )}

                {/* ============ TITLE SLIDE ============ */}
                {isTitleSlide ? (
                  <div className="flex flex-1 flex-col items-center justify-center text-center">
                    <EditableText
                      value={current.title}
                      onChange={(v) => updateSlide(selected, "title", v)}
                      className="text-lg font-bold text-foreground sm:text-2xl"
                      placeholder="Presentation Title"
                    />
                    <div className="mt-2">
                      <EditableText
                        value={current.subtitle}
                        onChange={(v) => updateSlide(selected, "subtitle", v)}
                        className="text-sm italic text-accent sm:text-base"
                        placeholder="Subtitle"
                      />
                    </div>
                    <div className="mt-3">
                      <EditableText
                        value={current.authors}
                        onChange={(v) => updateSlide(selected, "authors", v)}
                        className="text-sm text-gray-600"
                        placeholder="Authors"
                      />
                    </div>
                    <div className="mt-1">
                      <EditableText
                        value={current.venue}
                        onChange={(v) => updateSlide(selected, "venue", v)}
                        className="text-xs text-gray-400"
                        placeholder="Venue"
                      />
                    </div>
                    {hasFigure && (
                      <div className="mt-3 flex max-h-24 items-center justify-center overflow-hidden rounded-lg sm:max-h-32">
                        <img src={current.figureUrl!} alt="Figure" className="max-h-full max-w-full object-contain" />
                      </div>
                    )}
                  </div>

                ) : isThankYou ? (
                  /* ============ THANK YOU SLIDE ============ */
                  <div className="flex flex-1 flex-col items-center justify-center text-center">
                    <EditableText
                      value={current.title}
                      onChange={(v) => updateSlide(selected, "title", v)}
                      className="text-xl font-bold text-foreground sm:text-2xl"
                    />
                    {current.bullets.length > 0 && (
                      <ul className="mt-4 space-y-1 text-sm text-gray-500">
                        {current.bullets.map((b, j) => (
                          <li key={j}>{b}</li>
                        ))}
                      </ul>
                    )}
                  </div>

                ) : (
                  /* ============ CONTENT SLIDES ============ */
                  <ContentSlide
                    slide={current}
                    hasFigure={hasFigure}
                    hasTable={hasTable}
                    hasEquation={hasEquation}
                    hasKeyNumber={hasKeyNumber}
                    onUpdate={(field, value) => updateSlide(selected, field, value)}
                  />
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="mt-3 flex items-center gap-2">
              <button
                onClick={deleteSlide}
                disabled={slides.length <= 1}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-red-500 hover:bg-red-50 disabled:opacity-30"
              >
                Delete Slide
              </button>
              <span className="ml-auto text-[10px] text-gray-400">
                Click any text to edit &middot; Esc to cancel &middot; {"\u2318"}S to download
              </span>
            </div>
          </div>

          {/* Bottom nav */}
          <div className="mx-auto mt-6 flex w-full max-w-2xl items-center justify-between text-sm">
            <button
              onClick={() => setSelected((p) => Math.max(0, p - 1))}
              disabled={selected === 0}
              className="rounded-lg px-3 py-1.5 font-medium text-gray-500 hover:bg-white disabled:opacity-30"
            >
              &laquo; Prev
            </button>
            <span className="text-gray-400">
              Slide {selected + 1} of {slides.length}
              {backupCount > 0 && (
                <span className="text-gray-300">
                  {" "}({mainCount} + {backupCount} backup)
                </span>
              )}
            </span>
            <button
              onClick={() => setSelected((p) => Math.min(slides.length - 1, p + 1))}
              disabled={selected === slides.length - 1}
              className="rounded-lg px-3 py-1.5 font-medium text-gray-500 hover:bg-white disabled:opacity-30"
            >
              Next &raquo;
            </button>
          </div>
        </main>

        {/* == Right: context panel == */}
        <aside className="shrink-0 overflow-y-auto border-t border-gray-200 bg-white p-4 lg:w-72 lg:border-l lg:border-t-0 lg:p-5">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Speaker Notes
            </h3>
            <textarea
              value={draftNotes}
              onChange={(e) => setDraftNotes(e.target.value)}
              onBlur={() => {
                if (draftNotes !== current.speakerNotes) {
                  updateSlide(selected, "speakerNotes", draftNotes);
                }
              }}
              rows={6}
              className="mt-2 w-full resize-none rounded-lg border border-gray-200 p-3 text-sm leading-relaxed text-gray-600 outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
              placeholder="Add speaker notes..."
            />
            <p className="mt-1 text-xs text-gray-400">
              ~{current.speakingTime}s
            </p>
            {current.transition && (
              <p className="mt-1 text-xs text-gray-300">
                &rarr; {current.transition}
              </p>
            )}
          </section>

          {current.sourceSection && (
            <section className="mt-6">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                From the Paper
              </h3>
              <p className="mt-2 text-sm font-medium text-foreground">
                {current.sourceSection}
              </p>
            </section>
          )}

          <section className="mt-6">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Slide Info
            </h3>
            <dl className="mt-2 space-y-1 text-xs text-gray-500">
              <div className="flex justify-between">
                <dt>Type</dt>
                <dd className="font-medium">{typeLabel(current.contentType)}</dd>
              </div>
              <div className="flex justify-between">
                <dt>Layout</dt>
                <dd className="font-medium">{current.layout}</dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ContentSlide — dynamic layout with inline editing                  */
/* ------------------------------------------------------------------ */

function ContentSlide({
  slide,
  hasFigure,
  hasTable,
  hasEquation,
  hasKeyNumber,
  onUpdate,
}: {
  slide: Slide;
  hasFigure: boolean;
  hasTable: boolean;
  hasEquation: boolean;
  hasKeyNumber: boolean;
  onUpdate: (field: string, value: unknown) => void;
}) {
  const hasAnnotations = slide.annotations.length > 0;
  const hasBullets = slide.bullets.length > 0;
  const hasContextLine = !!slide.contextLine;
  const hasReferences = slide.references.length > 0;

  const isFigureTwoCol = hasFigure && hasAnnotations;
  const isStandaloneKeyNumber = hasKeyNumber && !hasFigure && !hasTable && !hasBullets && !hasEquation;

  return (
    <>
      {/* Title */}
      <div className="shrink-0">
        <EditableText
          value={slide.title}
          onChange={(v) => onUpdate("title", v)}
          className="text-sm font-bold leading-snug text-foreground sm:text-lg"
          placeholder="Click to add title"
        />
      </div>

      {/* Content area — dynamic order based on what exists */}
      <div className="mt-1 flex min-h-0 flex-1 flex-col overflow-hidden">

        {/* Standalone key number — centered */}
        {isStandaloneKeyNumber && (
          <div className="flex flex-1 flex-col items-center justify-center">
            <EditableText
              value={slide.keyNumber}
              onChange={(v) => onUpdate("keyNumber", v)}
              className="text-3xl font-bold text-accent sm:text-4xl"
            />
            <div className="mt-1">
              <EditableText
                value={slide.keyNumberContext}
                onChange={(v) => onUpdate("keyNumberContext", v)}
                className="text-xs text-gray-500"
                placeholder="Add context..."
              />
            </div>
            <AnnotationList
              annotations={slide.annotations}
              onChange={(anns) => onUpdate("annotations", anns)}
              className="mt-3 text-center"
            />
          </div>
        )}

        {/* Non-standalone key number — inline above other content */}
        {hasKeyNumber && !isStandaloneKeyNumber && (
          <div className="mb-1 flex shrink-0 flex-col items-center">
            <EditableText
              value={slide.keyNumber}
              onChange={(v) => onUpdate("keyNumber", v)}
              className="text-xl font-bold text-accent sm:text-2xl"
            />
            {slide.keyNumberContext && (
              <EditableText
                value={slide.keyNumberContext}
                onChange={(v) => onUpdate("keyNumberContext", v)}
                className="text-[10px] text-gray-500"
              />
            )}
          </div>
        )}

        {!isStandaloneKeyNumber && (
          <>
            {/* Table headline */}
            {hasTable && slide.tableHeadline && (
              <div className="shrink-0 mb-1">
                <EditableText
                  value={slide.tableHeadline}
                  onChange={(v) => onUpdate("tableHeadline", v)}
                  className="text-[10px] font-semibold text-gray-600"
                />
              </div>
            )}

            {/* Table — immediately after headline, editable cells */}
            {hasTable && (
              <div className="shrink-0">
                <EditableSlideTable
                  tableData={slide.tableData!}
                  onChange={(td) => onUpdate("tableData", td)}
                />
              </div>
            )}

            {/* Context line */}
            {hasContextLine && (
              <div className="shrink-0 mt-1">
                <EditableText
                  value={slide.contextLine}
                  onChange={(v) => onUpdate("contextLine", v)}
                  className="text-[10px] italic text-gray-500"
                />
              </div>
            )}

            {/* Annotations after table (no figure) */}
            {hasTable && hasAnnotations && (
              <AnnotationList
                annotations={slide.annotations}
                onChange={(anns) => onUpdate("annotations", anns)}
                className="mt-1"
              />
            )}

            {/* Figure + annotations: two-column */}
            {isFigureTwoCol && (
              <div className="flex min-h-0 flex-1 gap-3 mt-1 overflow-hidden">
                <div className="flex w-2/5 flex-col justify-center overflow-hidden">
                  <AnnotationList
                    annotations={slide.annotations}
                    onChange={(anns) => onUpdate("annotations", anns)}
                  />
                </div>
                <div className="flex w-3/5 items-center justify-center overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1">
                  <img src={slide.figureUrl!} alt="Figure" className="max-h-full max-w-full object-contain" />
                </div>
              </div>
            )}

            {/* Figure without annotations: full width */}
            {hasFigure && !isFigureTwoCol && (
              <div className="flex min-h-0 flex-1 items-center justify-center mt-1 overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1">
                <img src={slide.figureUrl!} alt="Figure" className="max-h-full max-w-full object-contain" />
              </div>
            )}

            {/* Equation */}
            {hasEquation && (
              <div className="shrink-0 mt-1 overflow-hidden rounded-lg bg-gray-50 px-2 py-1.5">
                <EditableText
                  value={slide.equationLatex}
                  onChange={(v) => onUpdate("equationLatex", v)}
                  className="text-[10px] font-mono text-gray-700"
                />
              </div>
            )}

            {/* Bullets — individually editable with add/delete */}
            <BulletList
              bullets={slide.bullets}
              onChange={(bs) => onUpdate("bullets", bs)}
              className="mt-1"
            />

            {/* Annotations for non-table, non-figure slides */}
            {!hasTable && !hasFigure && hasAnnotations && (
              <AnnotationList
                annotations={slide.annotations}
                onChange={(anns) => onUpdate("annotations", anns)}
                className="mt-1"
              />
            )}

            {/* References */}
            {hasReferences && (
              <p className="mt-auto shrink-0 truncate pt-1 text-[8px] text-gray-400">
                {slide.references.join(" \u00B7 ")}
              </p>
            )}
          </>
        )}
      </div>
    </>
  );
}
