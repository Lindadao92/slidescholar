"use client";

import { useState } from "react";
import Link from "next/link";

const TOTAL_SLIDES = 41;
const slides = Array.from({ length: TOTAL_SLIDES }, (_, i) => `/example-slides/slide-${i + 1}.png`);

export default function ExamplePage() {
  const [currentSlide, setCurrentSlide] = useState(0);

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="border-b border-gray-100 px-4 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <Link
            href="/"
            className="text-sm font-medium text-accent hover:underline"
          >
            &larr; Back
          </Link>
          <h1 className="text-lg font-bold tracking-tight">
            Example: Research Paper &rarr; Conference Slides
          </h1>
          <div className="w-12" />
        </div>
      </header>

      {/* Split view */}
      <div className="mx-auto flex max-w-7xl flex-col lg:flex-row lg:h-[calc(100vh-8rem)]">
        {/* LEFT: PDF */}
        <div className="flex flex-col border-b border-gray-200 lg:w-1/2 lg:border-b-0 lg:border-r">
          <div className="border-b border-gray-100 bg-gray-50 px-4 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Original Paper (PDF)
            </span>
          </div>
          <div className="flex-1 min-h-[50vh] lg:min-h-0">
            <iframe
              src="/sample-paper.pdf"
              className="h-full w-full"
              title="Sample research paper PDF"
            />
          </div>
        </div>

        {/* RIGHT: Slides */}
        <div className="flex flex-col lg:w-1/2">
          <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-4 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Generated Slides
            </span>
            <a
              href="/sample-presentation.pptx"
              download
              className="text-xs font-medium text-accent hover:underline"
            >
              Download .pptx
            </a>
          </div>

          <div className="flex flex-1 flex-col">
            {/* Slide display */}
            <div className="flex flex-1 items-center justify-center bg-gray-50 p-4">
              <img
                src={slides[currentSlide]}
                alt={`Slide ${currentSlide + 1}`}
                className="max-h-full max-w-full rounded-lg border border-gray-200 shadow-md"
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
