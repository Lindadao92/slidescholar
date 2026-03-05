"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

interface ParseResult {
  title?: string;
  authors?: string | string[];
  num_pages?: number;
  num_figures?: number;
  figures?: { url?: string; figure_label?: string; caption?: string; page?: number }[];
  structure?: string;
  [key: string]: unknown;
}

type TalkFormat = "lightning" | "conference" | "seminar";
type TemplateStyle = "minimal" | "academic";

const TALK_FORMATS: {
  value: TalkFormat;
  icon: string;
  label: string;
  desc: string;
}[] = [
  {
    value: "lightning",
    icon: "\u26A1",
    label: "Lightning Talk",
    desc: "5 minutes \u00B7 ~5 slides",
  },
  {
    value: "conference",
    icon: "\uD83C\uDF99\uFE0F",
    label: "Conference Talk",
    desc: "15 minutes \u00B7 ~12 slides",
  },
  {
    value: "seminar",
    icon: "\uD83D\uDCDA",
    label: "Seminar Talk",
    desc: "45 minutes \u00B7 ~25 slides",
  },
];

const TEMPLATE_STYLES: {
  value: TemplateStyle;
  label: string;
  desc: string;
  swatch: string;
}[] = [
  {
    value: "minimal",
    label: "Minimal",
    desc: "White, clean. Best for conference talks and workshops.",
    swatch: "bg-white border border-gray-300",
  },
  {
    value: "academic",
    label: "Academic",
    desc: "Blue accents with section indicators. Best for seminars and defenses.",
    swatch: "bg-blue-600",
  },
];

export default function ConfigurePage() {
  const router = useRouter();

  const [paper, setPaper] = useState<ParseResult | null>(null);
  const [format, setFormat] = useState<TalkFormat>("conference");
  const [template, setTemplate] = useState<TemplateStyle>("minimal");
  const [speakerNotes, setSpeakerNotes] = useState(true);
  const [qaSlides, setQaSlides] = useState(true);
  const [citations, setCitations] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const raw = sessionStorage.getItem("parseResult");
    if (!raw) {
      router.replace("/");
      return;
    }
    try {
      setPaper(JSON.parse(raw));
    } catch {
      router.replace("/");
    }
  }, [router]);

  if (!paper) return null;

  const figureCount = paper.num_figures ?? paper.figures?.length ?? 0;
  const pageCount = paper.num_pages ?? 0;
  const structureLabel = paper.structure ?? "IMRaD";

  const apiBase = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

  const handleGenerate = () => {
    const config = {
      format,
      template,
      speakerNotes,
      qaSlides,
      citations,
    };

    sessionStorage.setItem("generateConfig", JSON.stringify(config));
    router.push("/generate");
  };

  return (
    <div className="min-h-screen bg-gray-50/60">
      {/* Top bar */}
      <nav className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4">
          <button
            onClick={() => router.push("/")}
            className="text-sm font-medium text-gray-500 hover:text-foreground"
          >
            &larr; Back
          </button>
          <span className="text-lg font-bold tracking-tight">
            Slide<span className="text-accent">Scholar</span>
          </span>
          <span className="w-12" />
        </div>
      </nav>

      <main className="mx-auto max-w-3xl px-4 py-10">
        {/* Paper info card */}
        <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm sm:p-8">
          <h1 className="text-2xl font-bold leading-snug tracking-tight sm:text-3xl">
            {paper.title ?? "Untitled Paper"}
          </h1>

          {paper.authors && (
            <p className="mt-2 text-sm text-gray-500">
              {Array.isArray(paper.authors)
                ? paper.authors.join(", ")
                : paper.authors}
            </p>
          )}

          <p className="mt-3 text-sm text-gray-400">
            {pageCount > 0 && <>{pageCount} pages</>}
            {figureCount > 0 && <> &middot; {figureCount} figures detected</>}
            {structureLabel && <> &middot; {structureLabel} structure identified</>}
          </p>

          {/* Figure thumbnails */}
          {paper.figures && paper.figures.length > 0 && (
            <div className="mt-5 flex gap-3 overflow-x-auto pb-1">
              {paper.figures.slice(0, 6).map((fig, i) => {
                const src = fig.url
                  ? fig.url.startsWith("http") ? fig.url : `${apiBase}${fig.url}`
                  : null;
                return src ? (
                  <img
                    key={i}
                    src={src}
                    alt={fig.caption || `Figure ${i + 1}`}
                    className="h-20 w-28 shrink-0 rounded-lg border border-gray-200 object-cover"
                  />
                ) : (
                  <div
                    key={i}
                    className="flex h-20 w-28 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-200 text-xs text-gray-300"
                  >
                    Fig {i + 1}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Configuration */}
        <section className="mt-8 space-y-8">
          {/* Talk format */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
              Talk Format
            </h2>
            <div className="grid gap-3 sm:grid-cols-3">
              {TALK_FORMATS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => setFormat(f.value)}
                  className={`rounded-xl border-2 px-4 py-4 text-left transition-colors ${
                    format === f.value
                      ? "border-accent bg-blue-50"
                      : "border-gray-200 bg-white hover:border-gray-300"
                  }`}
                >
                  <span className="text-xl">{f.icon}</span>
                  <p className="mt-1 text-sm font-semibold">{f.label}</p>
                  <p className="text-xs text-gray-400">{f.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Template style */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
              Template Style
            </h2>
            <div className="flex gap-3">
              {TEMPLATE_STYLES.map((s) => (
                <button
                  key={s.value}
                  onClick={() => setTemplate(s.value)}
                  className={`flex items-center gap-3 rounded-xl border-2 px-4 py-3 transition-colors ${
                    template === s.value
                      ? "border-accent bg-blue-50"
                      : "border-gray-200 bg-white hover:border-gray-300"
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 rounded-full ${s.swatch}`}
                  />
                  <span>
                    <p className="text-sm font-semibold">{s.label}</p>
                    <p className="text-xs text-gray-400">{s.desc}</p>
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Options */}
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
              Options
            </h2>
            <div className="space-y-3">
              <Checkbox
                checked={speakerNotes}
                onChange={setSpeakerNotes}
                label="Include speaker notes"
              />
              <Checkbox
                checked={qaSlides}
                onChange={setQaSlides}
                label="Generate Q&A backup slides"
              />
              <Checkbox
                checked={citations}
                onChange={setCitations}
                label="Include citation references on slides"
              />
            </div>
          </div>
        </section>

        {/* Error */}
        {error && <p className="mt-6 text-sm text-red-600">{error}</p>}

        {/* Generate button */}
        <button
          onClick={handleGenerate}
          className="mt-10 flex w-full items-center justify-center rounded-xl bg-accent py-4 text-base font-semibold text-white transition-opacity hover:opacity-90"
        >
          Generate My Slides &rarr;
        </button>
      </main>
    </div>
  );
}

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-3 rounded-lg px-1 py-1 text-sm hover:bg-gray-50">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-gray-300 text-accent accent-accent"
      />
      <span>{label}</span>
    </label>
  );
}
