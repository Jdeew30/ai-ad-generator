import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "AI Ad Studio", template: "%s · AI Ad Studio" },
  description:
    "Transformez un brief produit en concept publicitaire, puis générez la vidéo avec le fournisseur d'IA vidéo de votre choix.",
};

export const viewport: Viewport = {
  themeColor: "#0e0d14",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
