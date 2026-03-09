"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import UpgradeModal from "../components/UpgradeModal";

interface ParseResult {
  paper_id?: string;
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

const FREE_FORMATS: TalkFormat[] = ["lightning"];

function isPro(f: TalkFormat) {
  return !FREE_FORMATS.includes(f);
}

export default function ConfigurePage() {
  return (
    <Suspense>
      <ConfigureInner />
    </Suspense>
  );
}

function ConfigureInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [paper, setPaper] = useState<ParseResult | null>(null);
  const [format, setFormat] = useState<TalkFormat>("conference");
  const [template, setTemplate] = useState<TemplateStyle>("minimal");
  const [speakerNotes, setSpeakerNotes] = useState(true);
  const [qaSlides, setQaSlides] = useState(true);
  const [citations, setCitations] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [subscribed, setSubscribed] = useState(false);
  const [showUpgrade, setShowUpgrade] = useState(false);
  const [showToast, setShowToast] = useState(false);

  // Check subscription status on load
  useEffect(() => {
    const stored = localStorage.getItem("ss_subscription");
    if (stored) {
      try {
        const data = JSON.parse(stored);
        if (data.subscribed && data.email) {
          // Verify with Stripe in background
          fetch(`/api/stripe/subscription-status?email=${encodeURIComponent(data.email)}`)
            .then((r) => r.json())
            .then((res) => {
              if (res.subscribed) {
                setSubscribed(true);
              } else {
                localStorage.removeItem("ss_subscription");
                setSubscribed(false);
              }
            })
            .catch(() => {
              // Offline/error — trust localStorage
              setSubscribed(true);
            });
          setSubscribed(true);
        }
      } catch {
        // ignore
      }
    }
  }, []);

  // Handle return from Stripe checkout
  useEffect(() => {
    const sub = searchParams.get("subscribed");
    const email = searchParams.get("email");
    if (sub === "true" && email) {
      localStorage.setItem(
        "ss_subscription",
        JSON.stringify({ subscribed: true, email })
      );
      setSubscribed(true);
      setShowToast(true);
      setTimeout(() => setShowToast(false), 5000);
      // Clean URL
      window.history.replaceState({}, "", "/configure");
    }
  }, [searchParams]);

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

  const handleGenerate = async () => {
    const config = {
      format,
      template,
      speakerNotes,
      qaSlides,
      citations,
    };
    sessionStorage.setItem("generateConfig", JSON.stringify(config));

    // Submit the job immediately
    setSubmitting(true);
    setError("");

    try {
      const res = await fetch(`${apiBase}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paper_id: paper.paper_id,
          talk_length: format,
          include_speaker_notes: speakerNotes,
          include_backup_slides: qaSlides,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Generation failed (${res.status})`);
      }

      const data = await res.json();
      if (!data.job_id) throw new Error("No job_id returned from server");

      // Store active job for the banner to pick up
      const activeJob = {
        jobId: data.job_id,
        startedAt: Date.now(),
        format: TALK_FORMATS.find((f) => f.value === format)?.label ?? format,
      };
      sessionStorage.setItem("activeJob", JSON.stringify(activeJob));

      // Navigate to the generate page for detailed progress
      router.push("/generate");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50/60">
      {/* Success toast */}
      {showToast && (
        <div className="fixed left-1/2 top-4 z-50 -translate-x-1/2 animate-[slideDown_0.3s_ease-out] rounded-xl border border-green-200 bg-green-50 px-6 py-3 shadow-lg">
          <p className="text-sm font-medium text-green-800">
            &#127881; Pro unlocked! All talk types available.
          </p>
        </div>
      )}

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
              {TALK_FORMATS.map((f) => {
                const locked = isPro(f.value) && !subscribed;
                return (
                  <button
                    key={f.value}
                    onClick={() => {
                      if (locked) {
                        setShowUpgrade(true);
                      } else {
                        setFormat(f.value);
                      }
                    }}
                    className={`relative rounded-xl border-2 px-4 py-4 text-left transition-colors ${
                      format === f.value
                        ? "border-accent bg-blue-50"
                        : locked
                          ? "border-gray-200 bg-gray-50 opacity-75 hover:border-gray-300 hover:opacity-100"
                          : "border-gray-200 bg-white hover:border-gray-300"
                    }`}
                  >
                    {locked && (
                      <span className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-blue-500 to-purple-500 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                        &#x1F512; Pro
                      </span>
                    )}
                    <span className="text-xl">{f.icon}</span>
                    <p className="mt-1 text-sm font-semibold">{f.label}</p>
                    <p className="text-xs text-gray-400">{f.desc}</p>
                  </button>
                );
              })}
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
          onClick={() => {
            if (isPro(format) && !subscribed) {
              setShowUpgrade(true);
            } else {
              handleGenerate();
            }
          }}
          disabled={submitting}
          className="mt-10 flex w-full items-center justify-center rounded-xl bg-accent py-4 text-base font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? (
            <>
              <svg className="mr-2 h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
              </svg>
              Starting...
            </>
          ) : (
            "Generate My Slides \u2192"
          )}
        </button>

        <UpgradeModal
          open={showUpgrade}
          onClose={() => setShowUpgrade(false)}
          onSubscribed={(email) => {
            localStorage.setItem(
              "ss_subscription",
              JSON.stringify({ subscribed: true, email })
            );
          }}
        />
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
