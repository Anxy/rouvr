import type { Metadata, Viewport } from "next";
import { Ubuntu_Sans, Geist_Mono } from "next/font/google";
import { Providers } from "@/components/layout/Providers";
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

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
      </head>
      <body className={`${ubuntuSans.variable} ${geistMono.variable} antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
