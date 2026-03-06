"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";

const POLL_INTERVAL_MS = 3_000;
const POLL_TIMEOUT_MS = 600_000;

interface ActiveJob {
  jobId: string;
  startedAt: number;
  format: string;
}

export default function GenerationBanner() {
  const router = useRouter();
  const pathname = usePathname();
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [status, setStatus] = useState<"polling" | "done" | "error">("polling");
  const [errorMsg, setErrorMsg] = useState("");
  const [dismissed, setDismissed] = useState(false);
  const [now, setNow] = useState(Date.now());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check sessionStorage for active job on mount and pathname change
  useEffect(() => {
    const raw = sessionStorage.getItem("activeJob");
    if (!raw) {
      setJob(null);
      return;
    }
    try {
      const parsed = JSON.parse(raw) as ActiveJob;
      setJob(parsed);
      setStatus("polling");
      setDismissed(false);
    } catch {
      sessionStorage.removeItem("activeJob");
      setJob(null);
    }
  }, [pathname]);

  // Poll for job completion
  useEffect(() => {
    if (!job || status !== "polling") return;

    const apiUrl = process.env.NEXT_PUBLIC_API_URL;

    const poll = async () => {
      if (Date.now() - job.startedAt > POLL_TIMEOUT_MS) {
        clearInterval(pollRef.current!);
        setStatus("error");
        setErrorMsg("Generation timed out. Please try again with a shorter talk format.");
        sessionStorage.removeItem("activeJob");
        return;
      }

      try {
        const res = await fetch(`${apiUrl}/api/jobs/${job.jobId}`);
        if (res.status === 404) {
          clearInterval(pollRef.current!);
          setStatus("error");
          setErrorMsg("Session expired (server restarted). Please re-upload your paper.");
          sessionStorage.removeItem("activeJob");
          return;
        }
        if (!res.ok) return;

        const data = await res.json();

        if (data.status === "done") {
          clearInterval(pollRef.current!);
          sessionStorage.setItem("generateResult", JSON.stringify(data));
          sessionStorage.removeItem("activeJob");
          setStatus("done");
        } else if (data.status === "error") {
          clearInterval(pollRef.current!);
          setStatus("error");
          setErrorMsg(data.detail || "Generation failed");
          sessionStorage.removeItem("activeJob");
        }
      } catch {
        // Network error — retry on next interval
      }
    };

    // Poll immediately, then on interval
    poll();
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [job, status]);

  // Tick elapsed time
  useEffect(() => {
    if (status !== "polling") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status]);

  const handleViewPresentation = useCallback(() => {
    setDismissed(true);
    router.push("/preview");
  }, [router]);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    setJob(null);
    sessionStorage.removeItem("activeJob");
  }, []);

  // Don't show on the generate page (it has its own UI) or if dismissed
  if (pathname === "/generate") return null;
  if (dismissed || !job) return null;

  // Don't show if already on preview and done
  if (pathname === "/preview" && status === "done") return null;

  const elapsed = Math.floor((now - job.startedAt) / 1000);
  const elapsedLabel =
    elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;

  return (
    <div className="fixed bottom-6 right-6 z-50" style={{ animation: "slideUp 0.3s ease-out" }}>
      <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-lg">
        {status === "polling" && (
          <>
            <span className="relative flex h-3 w-3 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/40" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">
                Generating presentation...
              </p>
              <p className="text-xs text-gray-400">
                {job.format} talk &middot; {elapsedLabel}
              </p>
            </div>
            <button
              onClick={() => router.push("/generate")}
              className="ml-2 shrink-0 text-xs font-medium text-accent hover:underline"
            >
              Details
            </button>
          </>
        )}

        {status === "done" && (
          <>
            <span className="flex h-6 w-6 shrink-0 items-center justify-center">
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
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">
                Presentation ready!
              </p>
            </div>
            <button
              onClick={handleViewPresentation}
              className="ml-2 shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90"
            >
              View now
            </button>
            <button
              onClick={handleDismiss}
              className="ml-1 shrink-0 text-gray-400 hover:text-gray-600"
            >
              &times;
            </button>
          </>
        )}

        {status === "error" && (
          <>
            <span className="flex h-5 w-5 shrink-0 items-center justify-center text-red-500">
              <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                  clipRule="evenodd"
                />
              </svg>
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-red-600">
                Generation failed
              </p>
              <p className="max-w-xs truncate text-xs text-red-400">
                {errorMsg}
              </p>
            </div>
            <button
              onClick={handleDismiss}
              className="ml-2 shrink-0 text-gray-400 hover:text-gray-600"
            >
              &times;
            </button>
          </>
        )}
      </div>
    </div>
  );
}
