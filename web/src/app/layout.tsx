import type { Metadata, Viewport } from "next";
import { Ubuntu_Sans, Geist_Mono } from "next/font/google";
import { Providers } from "@/components/layout/Providers";
import { Intro } from "@/components/layout/Intro";
import { Header } from "@/components/layout/Header";
import { Footer } from "@/components/layout/Footer";
import { SITE_DESCRIPTION, SITE_NAME, SITE_TITLE } from "@/lib/seo";
import { SITE_URL } from "@/lib/utils";
import "./globals.css";

const ubuntuSans = Ubuntu_Sans({
  variable: "--font-ubuntu-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: SITE_TITLE,
    template: `%s | ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  themeColor: "#040B13",
  colorScheme: "dark light",
};

const themeBoot = `(function(){var key="theme";function read(){try{var t=localStorage.getItem(key);if(t==="light"||t==="dark")return t;}catch(e){}return "dark";}function apply(){var t=read();var d=document.documentElement;var other=t==="light"?"dark":"light";if(!d.classList.contains(t)||d.classList.contains(other)){d.classList.remove("light","dark");d.classList.add(t);}d.style.colorScheme=t;d.setAttribute("data-theme",t);var meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.setAttribute("content",t==="light"?"#F3F6FA":"#040B13");}apply();new MutationObserver(apply).observe(document.documentElement,{attributes:true,attributeFilter:["class"]});document.addEventListener("DOMContentLoaded",apply);})();`;

const introBoot = `(function(){try{if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;if(sessionStorage.getItem("rouvr-intro-seen")==="1")return;document.documentElement.classList.add("intro-play");}catch(e){}})();`;

const introBootCss = `html:not(.intro-play) [data-intro-overlay]{display:none!important}html.intro-play,html.intro-play body{overflow:hidden}html.intro-play [data-intro-overlay]{position:fixed;inset:0;z-index:80;display:flex;align-items:center;justify-content:center;background:#040b13}html.light.intro-play [data-intro-overlay]{background:#f3f6fa}`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
        <script dangerouslySetInnerHTML={{ __html: introBoot }} />
        <style dangerouslySetInnerHTML={{ __html: introBootCss }} />
      </head>
      <body className={`${ubuntuSans.variable} ${geistMono.variable} antialiased`}>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-full focus:bg-brand focus:px-4 focus:py-2 focus:text-[#040b13]"
        >
          Skip to content
        </a>
        <Providers>
          <Intro />
          <Header />
          <main id="main">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
