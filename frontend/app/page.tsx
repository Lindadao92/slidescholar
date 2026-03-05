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

  const handleSubmit = async () => {
    if (!hasInput) return;
    setLoading(true);
    setError("");

    try {
      let res: Response;

      if (file) {
        const formData = new FormData();
        formData.append("file", file);
        res = await fetch("http://localhost:8000/api/parse", {
          method: "POST",
          body: formData,
        });
      } else {
        res = await fetch("http://localhost:8000/api/parse-arxiv", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arxiv_url: arxivUrl.trim() }),
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
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center px-4 py-16 sm:py-24">
      {/* Header */}
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          Slide<span className="text-accent">Scholar</span>
        </h1>
        <p className="mt-3 text-lg text-gray-500">
          Paper to Talk in 60 Seconds
        </p>
      </header>

      {/* Upload zone */}
      <div className="w-full max-w-xl">
        <label
          htmlFor="file-upload"
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-14 transition-colors ${
            dragActive
              ? "border-accent bg-blue-50"
              : "border-gray-300 hover:border-accent hover:bg-gray-50"
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
          className="w-full rounded-xl border border-gray-300 px-4 py-3 text-sm outline-none transition-colors placeholder:text-gray-400 focus:border-accent focus:ring-2 focus:ring-accent/20"
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
      </div>

      {/* Feature bullets */}
      <ul className="mt-16 grid max-w-xl gap-4 text-sm text-gray-500 sm:grid-cols-3">
        <li className="flex flex-col items-center text-center">
          <span className="mb-1 text-2xl">&#128196;</span>
          <span className="font-medium text-foreground">Upload PDF</span>
          <span className="mt-0.5">or paste an arXiv link</span>
        </li>
        <li className="flex flex-col items-center text-center">
          <span className="mb-1 text-2xl">&#9889;</span>
          <span className="font-medium text-foreground">AI-Powered</span>
          <span className="mt-0.5">extracts key insights</span>
        </li>
        <li className="flex flex-col items-center text-center">
          <span className="mb-1 text-2xl">&#127908;</span>
          <span className="font-medium text-foreground">Talk-Ready</span>
          <span className="mt-0.5">slides in 60 seconds</span>
        </li>
      </ul>
    </div>
  );
}
