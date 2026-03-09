"use client";

import { useState } from "react";

interface UpgradeModalProps {
  open: boolean;
  onClose: () => void;
  onSubscribed: (email: string) => void;
}

export default function UpgradeModal({
  open,
  onClose,
  onSubscribed,
}: UpgradeModalProps) {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.includes("@")) {
      setError("Please enter a valid email address");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/stripe/create-checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Failed to start checkout");
      }

      // Save email for post-checkout verification
      localStorage.setItem("ss_checkout_email", email);
      onSubscribed(email);

      // Redirect to Stripe Checkout
      window.location.href = data.url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative mx-4 w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-gray-400 hover:text-gray-600"
        >
          <svg
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>

        <div className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-blue-50">
            <span className="text-2xl">&#x1F680;</span>
          </div>
          <h2 className="text-xl font-bold tracking-tight">
            Unlock All Talk Types
          </h2>
          <p className="mt-2 text-sm text-gray-500">
            Get Conference, Seminar and more talk formats with Pro.
          </p>
        </div>

        <div className="mt-6 rounded-xl border border-blue-100 bg-blue-50/50 p-4">
          <div className="flex items-baseline justify-between">
            <span className="text-sm font-semibold text-gray-700">
              SlideScholar Pro
            </span>
            <span>
              <span className="text-2xl font-bold">&euro;4.99</span>
              <span className="text-sm text-gray-500">/month</span>
            </span>
          </div>
          <ul className="mt-3 space-y-1.5 text-sm text-gray-600">
            <li className="flex items-center gap-2">
              <span className="text-green-500">&#x2713;</span>
              All talk formats (Conference, Seminar, etc.)
            </li>
            <li className="flex items-center gap-2">
              <span className="text-green-500">&#x2713;</span>
              Unlimited slide generations
            </li>
            <li className="flex items-center gap-2">
              <span className="text-green-500">&#x2713;</span>
              Cancel anytime
            </li>
          </ul>
        </div>

        <form onSubmit={handleSubmit} className="mt-6 space-y-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="your@email.com"
            required
            className="w-full rounded-lg border border-gray-300 px-4 py-3 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
          />

          {error && <p className="text-sm text-red-500">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center rounded-lg bg-accent py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
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
                Redirecting to checkout...
              </>
            ) : (
              "Start for \u20AC4.99/month"
            )}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-gray-400">
          Secure payment via Stripe. Cancel anytime.
        </p>
      </div>
    </div>
  );
}
