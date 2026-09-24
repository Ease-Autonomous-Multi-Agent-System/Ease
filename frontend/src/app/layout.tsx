import type { Metadata } from "next";
import { Space_Grotesk } from "next/font/google";
import { headers } from "next/headers";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const font = Space_Grotesk({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "Ease — supervised web automation",
  description: "Plain-English goals, planned and executed by agents, with you approving anything irreversible.",
};

// Applies a remembered light/dark choice before first paint (no flash). Runs under the CSP nonce.
const THEME_BOOT =
  "try{var t=localStorage.getItem('ease-theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}";

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" className={font.variable} suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
