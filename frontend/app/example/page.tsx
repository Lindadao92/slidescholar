"use client";

import { useState, useRef, useCallback } from "react";
import Link from "next/link";

type View = "side-by-side" | "paper" | "slides";

export default function ExamplePage() {
  const [view, setView] = useState<View>("side-by-side");
  const containerRef = useRef<HTMLDivElement>(null);
  const [leftWidth, setLeftWidth] = useState(50);
  const isDragging = useRef(false);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isDragging.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    setLeftWidth(Math.min(75, Math.max(25, pct)));
  }, []);

  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
    document.removeEventListener("mousemove", handleMouseMove);
    document.removeEventListener("mouseup", handleMouseUp);
  }, [handleMouseMove]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    },
    [handleMouseMove, handleMouseUp]
  );

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

      {/* Content */}
      {view === "side-by-side" ? (
        <div
          ref={containerRef}
          className="flex flex-1 overflow-hidden"
          style={{ height: "calc(100vh - 7rem)" }}
        >
          {/* Left: Paper PDF */}
          <div
            style={{ width: `${leftWidth}%` }}
            className="flex h-full flex-col overflow-hidden"
          >
            <div className="border-b border-gray-100 bg-gray-50 px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Original Paper
              </span>
            </div>
            <iframe
              src="/sample-paper.pdf"
              className="h-full w-full border-0"
              title="Sample research paper PDF"
            />
          </div>

          {/* Draggable divider */}
          <div
            onMouseDown={handleMouseDown}
            className="w-1 shrink-0 cursor-col-resize bg-gray-300 transition-colors hover:bg-blue-400"
          />

          {/* Right: Slides PDF */}
          <div
            style={{ width: `${100 - leftWidth}%` }}
            className="flex h-full flex-col overflow-hidden"
          >
            <div className="border-b border-gray-100 bg-gray-50 px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                Generated Slides
              </span>
            </div>
            <iframe
              src="/sample-presentation.pdf"
              className="h-full w-full border-0"
              title="Generated presentation slides"
            />
          </div>
        </div>
      ) : (
        <div
          className="flex flex-1 flex-col overflow-hidden"
          style={{ height: "calc(100vh - 7rem)" }}
        >
          <div className="border-b border-gray-100 bg-gray-50 px-4 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              {showPaper ? "Original Paper" : "Generated Slides"}
            </span>
          </div>
          <iframe
            src={showPaper ? "/sample-paper.pdf" : "/sample-presentation.pdf"}
            className="h-full w-full border-0"
            title={showPaper ? "Sample research paper PDF" : "Generated presentation slides"}
          />
        </div>
      )}

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
