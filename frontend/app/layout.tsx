import type { Metadata } from "next";
import Script from "next/script";
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
      <head>
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=AW-16990114177"
          strategy="afterInteractive"
        />
        <Script id="gtag-init" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());
            gtag('config', 'AW-16990114177');
          `}
        </Script>
      </head>
      <body className="antialiased">
        {children}
        <GenerationBanner />
      </body>
    </html>
  );
}
