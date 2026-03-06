import type { Metadata } from "next";
import "./globals.css";
import GenerationBanner from "./components/GenerationBanner";

export const metadata: Metadata = {
  title: "SlideScholar",
  description: "Paper to Talk in 60 Seconds",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        {children}
        <GenerationBanner />
      </body>
    </html>
  );
}
