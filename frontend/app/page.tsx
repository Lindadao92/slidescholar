"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [arxivUrl, setArxivUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dragActive, setDragActive] = useState(false);

  const hasInput = file || arxivUrl.trim();

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    setError("");

    const droppedFile = e.dataTransfer.files?.[0];
    if (!droppedFile) return;

    if (droppedFile.type !== "application/pdf") {
      setError("Please upload a PDF file.");
      return;
    }
    if (droppedFile.size > 50 * 1024 * 1024) {
      setError("File must be under 50 MB.");
      return;
    }

    setFile(droppedFile);
    setArxivUrl("");
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError("");
    const selected = e.target.files?.[0];
    if (!selected) return;

    if (selected.type !== "application/pdf") {
      setError("Please upload a PDF file.");
      return;
    }
    if (selected.size > 50 * 1024 * 1024) {
      setError("File must be under 50 MB.");
      return;
    }

    setFile(selected);
    setArxivUrl("");
  };

  const handleArxivChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setArxivUrl(e.target.value);
    setFile(null);
    setError("");
  };

  const isValidArxivUrl = (url: string): boolean => {
    return /arxiv\.org\/(?:abs|pdf)\/\d+\.\d+/i.test(url.trim());
  };

  const handleSubmit = async () => {
    if (!hasInput) return;

    if (!file && !isValidArxivUrl(arxivUrl)) {
      setError("Please enter a valid arXiv URL (e.g. https://arxiv.org/abs/2301.00001)");
      return;
    }

    setLoading(true);
    setError("");

    // Clear stale session data before new upload
    sessionStorage.removeItem("parseResult");
    sessionStorage.removeItem("generateConfig");
    sessionStorage.removeItem("generateResult");

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 120_000); // 2 min timeout

    try {
      let res: Response;

      if (file) {
        const formData = new FormData();
        formData.append("file", file);
        res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/parse`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        });
      } else {
        res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/parse-arxiv`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arxiv_url: arxivUrl.trim() }),
          signal: controller.signal,
        });
      }

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }

      const data = await res.json();
      sessionStorage.setItem("parseResult", JSON.stringify(data));
      router.push("/configure");
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("Request timed out. The paper may be too large — please try a smaller file.");
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong.");
      }
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      {/* ================================================================ */}
      {/*  HERO                                                            */}
      {/* ================================================================ */}
      <section className="px-4 pt-16 pb-12 sm:pt-24 sm:pb-16">
        <div className="mx-auto max-w-4xl text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl lg:text-6xl">
            Turn your research paper into conference slides
            <span className="text-accent"> — in 60 seconds</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-gray-500">
            Assertion-evidence format. Figures extracted. Speaker notes included.
            No signup needed.
          </p>
        </div>

        {/* Slide thumbnails preview */}
        <div className="mx-auto mt-12 flex max-w-3xl items-center justify-center gap-4 px-4 sm:gap-6">
          <MiniSlide rotate={-6} className="hidden sm:block">
            <div className="mb-1 h-1 w-8 rounded bg-accent" />
            <div className="h-1.5 w-20 rounded bg-gray-800" />
            <div className="mt-auto text-center">
              <div className="mx-auto h-1 w-14 rounded bg-gray-300" />
              <div className="mx-auto mt-0.5 h-0.5 w-10 rounded bg-gray-200" />
            </div>
          </MiniSlide>

          <MiniSlide rotate={-2}>
            <div className="h-1 w-16 rounded bg-gray-800" />
            <div className="mt-1 flex flex-1 gap-1.5">
              <div className="flex w-1/2 flex-col gap-0.5">
                <div className="h-0.5 w-full rounded bg-gray-300" />
                <div className="h-0.5 w-4/5 rounded bg-gray-300" />
                <div className="h-0.5 w-full rounded bg-gray-300" />
              </div>
              <div className="w-1/2 rounded bg-blue-100" />
            </div>
          </MiniSlide>

          <MiniSlide rotate={1} highlight>
            <div className="h-1 w-14 rounded bg-gray-800" />
            <div className="mt-1 flex-1 rounded border border-gray-200">
              <div className="flex gap-px border-b border-gray-200 bg-accent/10 px-1 py-0.5">
                <div className="h-0.5 w-4 rounded bg-accent/40" />
                <div className="h-0.5 w-4 rounded bg-accent/40" />
                <div className="h-0.5 w-4 rounded bg-accent/40" />
              </div>
              {[0, 1, 2].map((r) => (
                <div key={r} className={`flex gap-px px-1 py-0.5 ${r % 2 ? "bg-gray-50" : ""}`}>
                  <div className="h-0.5 w-4 rounded bg-gray-300" />
                  <div className="h-0.5 w-4 rounded bg-gray-300" />
                  <div className="h-0.5 w-4 rounded bg-gray-300" />
                </div>
              ))}
            </div>
          </MiniSlide>

          <MiniSlide rotate={4}>
            <div className="h-1 w-12 rounded bg-gray-800" />
            <div className="flex flex-1 flex-col items-center justify-center">
              <span className="text-lg font-bold leading-none text-accent">3.2x</span>
              <div className="mt-0.5 h-0.5 w-10 rounded bg-gray-300" />
            </div>
          </MiniSlide>

          <MiniSlide rotate={7} className="hidden sm:block">
            <div className="h-1 w-16 rounded bg-gray-800" />
            <div className="mt-1 flex flex-1 flex-col gap-0.5">
              <div className="flex items-start gap-1">
                <div className="mt-0.5 h-0.5 w-0.5 shrink-0 rounded-full bg-accent" />
                <div className="h-0.5 w-full rounded bg-gray-300" />
              </div>
              <div className="flex items-start gap-1">
                <div className="mt-0.5 h-0.5 w-0.5 shrink-0 rounded-full bg-accent" />
                <div className="h-0.5 w-4/5 rounded bg-gray-300" />
              </div>
              <div className="flex items-start gap-1">
                <div className="mt-0.5 h-0.5 w-0.5 shrink-0 rounded-full bg-accent" />
                <div className="h-0.5 w-full rounded bg-gray-300" />
              </div>
            </div>
          </MiniSlide>
        </div>
      </section>

      {/* ================================================================ */}
      {/*  UPLOAD BOX                                                      */}
      {/* ================================================================ */}
      <section id="upload" className="border-t border-gray-100 bg-gray-50/60 px-4 py-16">
        <div className="mx-auto max-w-xl">
          <h2 className="mb-8 text-center text-2xl font-bold tracking-tight sm:text-3xl">
            Try it now
          </h2>

          <label
            htmlFor="file-upload"
            onDragEnter={handleDrag}
            onDragOver={handleDrag}
            onDragLeave={handleDrag}
            onDrop={handleDrop}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-14 transition-colors ${
              dragActive
                ? "border-accent bg-blue-50"
                : "border-gray-300 bg-white hover:border-accent hover:bg-gray-50"
            }`}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="mb-3 h-10 w-10 text-gray-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16"
              />
            </svg>
            <span className="text-sm font-medium text-gray-600">
              Drag & drop a PDF here, or{" "}
              <span className="text-accent underline">browse</span>
            </span>
            <span className="mt-1 text-xs text-gray-400">
              PDF up to 50 MB
            </span>
            <input
              id="file-upload"
              type="file"
              accept=".pdf,application/pdf"
              className="hidden"
              onChange={handleFileSelect}
            />
          </label>

          {/* File info */}
          {file && (
            <div className="mt-3 flex items-center justify-between rounded-lg bg-blue-50 px-4 py-2 text-sm">
              <span className="truncate font-medium text-accent">
                {file.name}
              </span>
              <button
                onClick={() => setFile(null)}
                className="ml-3 shrink-0 text-gray-400 hover:text-gray-600"
              >
                &times;
              </button>
            </div>
          )}

          {/* Divider */}
          <div className="my-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-gray-200" />
            <span className="text-xs font-medium text-gray-400">OR</span>
            <div className="h-px flex-1 bg-gray-200" />
          </div>

          {/* ArXiv URL input */}
          <input
            type="url"
            placeholder="Paste an arXiv URL (e.g. https://arxiv.org/abs/2301.00001)"
            value={arxivUrl}
            onChange={handleArxivChange}
            className="w-full rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm outline-none transition-colors placeholder:text-gray-400 focus:border-accent focus:ring-2 focus:ring-accent/20"
          />

          {/* Error */}
          {error && (
            <p className="mt-3 text-sm text-red-600">{error}</p>
          )}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={!hasInput || loading}
            className="mt-6 flex w-full items-center justify-center rounded-xl bg-accent py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? (
              <>
                <svg
                  className="mr-2 h-4 w-4 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                  />
                </svg>
                Processing...
              </>
            ) : (
              "Continue \u2192"
            )}
          </button>

          {/* Sub-CTA */}
          <p className="mt-4 text-center text-xs text-gray-400">
            Free &middot; No signup required
          </p>
          <p className="mt-2 text-center">
            <a href="/example" className="text-xs font-medium text-accent hover:underline">
              See example output &rarr;
            </a>
          </p>
        </div>
      </section>

      {/* ================================================================ */}
      {/*  FEATURES                                                        */}
      {/* ================================================================ */}
      <section className="px-4 py-16">
        <div className="mx-auto max-w-4xl">
          <h2 className="text-center text-sm font-semibold uppercase tracking-wide text-gray-400">
            What you get
          </h2>
          <div className="mt-8 grid gap-6 sm:grid-cols-2">
            <FeatureCard
              title="Assertion-Evidence Titles"
              desc="Every slide makes a claim, not a topic label. The format top researchers use."
            />
            <FeatureCard
              title="Your Figures, Properly Placed"
              desc="Extracted from your PDF with correct aspect ratios and captions."
            />
            <FeatureCard
              title="Editable Tables"
              desc="Paper tables become clean PowerPoint tables with key results bolded."
            />
            <FeatureCard
              title="Speaker Notes with Timing"
              desc="Conversational notes with [pause] markers and per-slide time budgets."
            />
          </div>
        </div>
      </section>

      {/* ================================================================ */}
      {/*  HOW IT WORKS                                                    */}
      {/* ================================================================ */}
      <section className="border-t border-gray-100 bg-gray-50/60 px-4 py-16">
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center text-sm font-semibold uppercase tracking-wide text-gray-400">
            How it works
          </h2>
          <div className="mt-8 grid gap-8 sm:grid-cols-3">
            <Step number="1" title="Upload your paper" desc="PDF or arXiv URL" />
            <Step number="2" title="Pick your talk format" desc="5 / 15 / 45 minutes" />
            <Step number="3" title="Edit inline & download" desc="Tweak any slide, then export .pptx" />
          </div>
          <p className="mt-6 text-center text-sm font-medium text-accent">
            Total time: under 2 minutes
          </p>
        </div>
      </section>

      {/* ================================================================ */}
      {/*  COMPARISON TABLE                                                */}
      {/* ================================================================ */}
      <section className="px-4 py-16">
        <div className="mx-auto max-w-4xl">
          <h2 className="text-center text-sm font-semibold uppercase tracking-wide text-gray-400">
            How SlideScholar compares
          </h2>
          <div className="mt-8 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left">
                  <th className="py-3 pr-4 font-medium text-gray-500">Feature</th>
                  <th className="px-4 py-3 font-semibold text-accent">SlideScholar</th>
                  <th className="px-4 py-3 font-medium text-gray-500">ChatGPT</th>
                  <th className="px-4 py-3 font-medium text-gray-500">Gamma</th>
                  <th className="px-4 py-3 font-medium text-gray-500">SlidesAI</th>
                </tr>
              </thead>
              <tbody className="text-gray-600">
                <CompareRow feature="Paper structure awareness" ss gamma slides chatgpt={false} />
                <CompareRow feature="Figure extraction" ss chatgpt={false} gamma={false} slides={false} />
                <CompareRow feature="Editable tables" ss chatgpt={false} gamma={false} slides={false} />
                <CompareRow feature="Speaker notes" ss chatgpt gamma={false} slides={false} />
                <CompareRow feature="Talk length adaptation" ss chatgpt={false} gamma={false} slides={false} />
                <CompareRow feature=".pptx export" ss chatgpt={false} gamma slides />
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ================================================================ */}
      {/*  FOOTER                                                          */}
      {/* ================================================================ */}
      <footer className="border-t border-gray-100 px-4 py-8 text-center text-xs text-gray-400">
        Slide<span className="font-semibold text-accent">Scholar</span> &middot; Built for researchers, by researchers.
        <span className="mx-2">&middot;</span>
        <a href="mailto:linda@failfasterventures.com" className="text-gray-400 hover:text-accent hover:underline">
          Customer Support
        </a>
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function MiniSlide({
  children,
  rotate = 0,
  highlight = false,
  className = "",
}: {
  children: React.ReactNode;
  rotate?: number;
  highlight?: boolean;
  className?: string;
}) {
  return (
    <div
      className={`flex aspect-video w-28 flex-col rounded-lg border bg-white p-2 shadow-md sm:w-36 sm:p-3 ${
        highlight ? "border-accent/30 shadow-lg ring-1 ring-accent/10" : "border-gray-200"
      } ${className}`}
      style={{ transform: `rotate(${rotate}deg)` }}
    >
      {children}
    </div>
  );
}

function FeatureCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="mt-1 text-sm text-gray-500">{desc}</p>
    </div>
  );
}

function Step({ number, title, desc }: { number: string; title: string; desc: string }) {
  return (
    <div className="flex flex-col items-center text-center">
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-sm font-bold text-white">
        {number}
      </span>
      <h3 className="mt-3 text-sm font-semibold text-foreground">{title}</h3>
      <p className="mt-1 text-xs text-gray-500">{desc}</p>
    </div>
  );
}

function CompareRow({
  feature,
  ss = false,
  chatgpt = false,
  gamma = false,
  slides = false,
}: {
  feature: string;
  ss?: boolean;
  chatgpt?: boolean;
  gamma?: boolean;
  slides?: boolean;
}) {
  const yes = <span className="text-green-600">&#10003;</span>;
  const no = <span className="text-gray-300">&#10005;</span>;
  return (
    <tr className="border-b border-gray-100">
      <td className="py-2.5 pr-4 font-medium">{feature}</td>
      <td className="px-4 py-2.5 text-center">{ss ? yes : no}</td>
      <td className="px-4 py-2.5 text-center">{chatgpt ? yes : no}</td>
      <td className="px-4 py-2.5 text-center">{gamma ? yes : no}</td>
      <td className="px-4 py-2.5 text-center">{slides ? yes : no}</td>
    </tr>
  );
}
