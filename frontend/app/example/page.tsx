"use client";

import { useState } from "react";
import Link from "next/link";

const TOTAL_SLIDES = 41;
const slides = Array.from(
  { length: TOTAL_SLIDES },
  (_, i) => `/example-slides/slide-${i + 1}.png`
);

type View = "side-by-side" | "paper" | "slides";

export default function ExamplePage() {
  const [currentSlide, setCurrentSlide] = useState(0);
  const [view, setView] = useState<View>("side-by-side");

  const showPaper = view === "side-by-side" || view === "paper";
  const showSlides = view === "side-by-side" || view === "slides";

  return (
    <div className="flex min-h-screen flex-col bg-white">
      {/* Header */}
      <header className="border-b border-gray-100 px-4 py-3">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <Link
            href="/"
            className="text-sm font-medium text-accent hover:underline"
          >
            &larr; Back
          </Link>
          <h1 className="hidden text-lg font-bold tracking-tight sm:block">
            Example: Research Paper &rarr; Conference Slides
          </h1>

          {/* View toggle */}
          <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5 text-xs font-medium">
            <button
              onClick={() => setView("paper")}
              className={`rounded-md px-3 py-1.5 transition-colors ${
                view === "paper"
                  ? "bg-white text-accent shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              Paper
            </button>
            <button
              onClick={() => setView("side-by-side")}
              className={`rounded-md px-3 py-1.5 transition-colors ${
                view === "side-by-side"
                  ? "bg-white text-accent shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              Side by Side
            </button>
            <button
              onClick={() => setView("slides")}
              className={`rounded-md px-3 py-1.5 transition-colors ${
                view === "slides"
                  ? "bg-white text-accent shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              Slides
            </button>
          </div>
        </div>
      </header>

      {/* Split view */}
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col lg:flex-row">
        {/* LEFT: PDF */}
        {showPaper && (
          <div
            className={`flex flex-col border-b border-gray-200 lg:border-b-0 ${
              view === "side-by-side"
                ? "lg:w-1/2 lg:border-r"
                : "w-full"
            }`}
          >
            <div className="border-b border-gray-100 bg-gray-50 px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Original Paper
              </span>
            </div>
            <div className="min-h-[50vh] flex-1 lg:min-h-0">
              <iframe
                src="/sample-paper.pdf"
                className="h-full w-full"
                title="Sample research paper PDF"
              />
            </div>
          </div>
        )}

        {/* Divider (side-by-side only, desktop) */}
        {view === "side-by-side" && (
          <div className="hidden w-1 cursor-col-resize bg-gray-200 hover:bg-accent/40 lg:block" />
        )}

        {/* RIGHT: Slides */}
        {showSlides && (
          <div
            className={`flex flex-col ${
              view === "side-by-side" ? "lg:w-1/2" : "w-full"
            }`}
          >
            <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Generated Slides
              </span>
              <a
                href="/sample-presentation.pptx"
                download
                className="flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 transition-colors hover:border-accent hover:text-accent"
                title="Download .pptx"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-3.5 w-3.5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3"
                  />
                </svg>
                .pptx
              </a>
            </div>

            <div className="flex flex-1 flex-col">
              {/* Slide display */}
              <div className="flex flex-1 items-center justify-center bg-gray-50 p-2 sm:p-4">
                <img
                  src={slides[currentSlide]}
                  alt={`Slide ${currentSlide + 1}`}
                  className="w-full rounded-lg border border-gray-200 shadow-md"
                  style={{ maxHeight: "calc(100vh - 12rem)" }}
                />
              </div>

              {/* Navigation */}
              <div className="flex items-center justify-center gap-4 border-t border-gray-100 px-4 py-3">
                <button
                  onClick={() => setCurrentSlide((s) => Math.max(0, s - 1))}
                  disabled={currentSlide === 0}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  &larr; Prev
                </button>
                <span className="text-sm text-gray-500">
                  {currentSlide + 1} / {slides.length}
                </span>
                <button
                  onClick={() =>
                    setCurrentSlide((s) => Math.min(slides.length - 1, s + 1))
                  }
                  disabled={currentSlide === slides.length - 1}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Next &rarr;
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* CTA */}
      <div className="border-t border-gray-100 bg-gray-50/60 px-4 py-8 text-center">
        <Link
          href="/#upload"
          className="inline-flex items-center rounded-xl bg-accent px-6 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90"
        >
          Try with your own paper &rarr;
        </Link>
      </div>
    </div>
  );
}
