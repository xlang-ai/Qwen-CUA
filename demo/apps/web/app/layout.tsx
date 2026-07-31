import type { Metadata } from "next";

import "./console.css";

export const metadata: Metadata = {
  title: "Qwen CUA · Operator Console",
  description: "Run and inspect Qwen computer-use browser agents.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
