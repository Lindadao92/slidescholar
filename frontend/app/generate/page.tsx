"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const STEPS = [
  { label: "Parsing paper structure", delayMs: 2_000 },
  { label: "Extracting figures", delayMs: 5_000 },
  { label: "Identifying key findings", delayMs: 10_000 },
  { label: "Building slide narrative", delayMs: 18_000 },
  { label: "Assembling presentation", delayMs: 28_000 },
  { label: "Writing speaker notes", delayMs: 40_000 },
];

const FORMAT_META: Record<string, { length: string; slides: string }> = {
  lightning: { length: "5-minute", slides: "5" },
  conference: { length: "15-minute", slides: "12" },
  seminar: { length: "45-minute", slides: "25" },
};

export default function GeneratePage() {
  const router = useRouter();
  const [completedStep, setCompletedStep] = useState(-1);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const startedRef = useRef(false);

  const configRaw =
    typeof window !== "undefined"
      ? sessionStorage.getItem("generateConfig")
      : null;
  const config = configRaw ? JSON.parse(configRaw) : null;
  const meta = FORMAT_META[config?.format] ?? FORMAT_META.conference;

  // Simulated step progression
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    STEPS.forEach((step, i) => {
      timers.push(setTimeout(() => setCompletedStep(i), step.delayMs));
    });
    return () => timers.forEach(clearTimeout);
  }, []);

  // API call
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    const paperRaw = sessionStorage.getItem("parseResult");
    if (!paperRaw || !configRaw) {
      router.replace("/");
      return;
    }

    const paper = JSON.parse(paperRaw);

    (async () => {
      try {
        const res = await fetch("http://localhost:8000/api/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            paper_id: paper.paper_id,
            talk_length: config.format,
            include_speaker_notes: config.speakerNotes,
            include_backup_slides: config.qaSlides,
          }),
        });

        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail || `Generation failed (${res.status})`);
        }

        const data = await res.json();
        sessionStorage.setItem("generateResult", JSON.stringify(data));

        // Complete all remaining steps visually then redirect
        setCompletedStep(STEPS.length - 1);
        setDone(true);
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : "Something went wrong."
        );
      }
    })();
  }, [router, config, configRaw]);

  // Redirect after done animation settles
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => router.push("/preview"), 800);
    return () => clearTimeout(t);
  }, [done, router]);

  // Determine which step is "active" (first uncompleted)
  const activeStep = completedStep + 1;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <p className="mb-10 text-center text-lg font-bold tracking-tight">
          Slide<span className="text-accent">Scholar</span>
        </p>

        {/* Steps */}
        <ol className="space-y-4">
          {STEPS.map((step, i) => {
            const isCompleted = i <= completedStep;
            const isActive = i === activeStep && !error;

            return (
              <li
                key={i}
                className={`flex items-center gap-3 transition-opacity duration-500 ${
                  isCompleted || isActive ? "opacity-100" : "opacity-40"
                }`}
              >
                {/* Indicator */}
                <span className="flex h-6 w-6 shrink-0 items-center justify-center">
                  {isCompleted ? (
                    <svg
                      className="h-5 w-5 text-emerald-500"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                    >
                      <path
                        fillRule="evenodd"
                        d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                        clipRule="evenodd"
                      />
                    </svg>
                  ) : isActive ? (
                    <span className="relative flex h-5 w-5 items-center justify-center">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/30" />
                      <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
                    </span>
                  ) : (
                    <span className="inline-flex h-3 w-3 rounded-full border-2 border-gray-300" />
                  )}
                </span>

                {/* Label */}
                <span
                  className={`text-sm ${
                    isCompleted
                      ? "font-medium text-foreground"
                      : isActive
                        ? "font-medium text-accent"
                        : "text-gray-400"
                  }`}
                >
                  {step.label}
                </span>
              </li>
            );
          })}
        </ol>

        {/* Summary line */}
        <p className="mt-10 text-center text-sm text-gray-400">
          Creating your {meta.length} conference talk &middot; ~{meta.slides}{" "}
          slides
        </p>

        {/* Pulse dots */}
        {!error && !done && (
          <div className="mt-4 flex items-center justify-center gap-1.5">
            <span
              className="h-1.5 w-1.5 rounded-full bg-accent/60 animate-pulse"
              style={{ animationDelay: "0ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-accent/60 animate-pulse"
              style={{ animationDelay: "300ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-accent/60 animate-pulse"
              style={{ animationDelay: "600ms" }}
            />
          </div>
        )}

        {/* Error state */}
        {error && (
          <div className="mt-8 text-center">
            <p className="text-sm text-red-600">{error}</p>
            <button
              onClick={() => {
                setError("");
                setCompletedStep(-1);
                startedRef.current = false;
                // Trigger re-mount of the API effect
                router.replace("/generate");
              }}
              className="mt-4 rounded-lg bg-accent px-6 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Try Again
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
