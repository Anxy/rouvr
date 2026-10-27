import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async redirects() {
    return [
      { source: "/early-access", destination: "/contact", permanent: true },
      { source: "/product", destination: "/", permanent: true },
      { source: "/how-it-works", destination: "/#how-it-works", permanent: true },
      { source: "/trust", destination: "/#trust", permanent: true },
      { source: "/scope", destination: "/#pack", permanent: true },
      { source: "/platform", destination: "/#capabilities", permanent: true },
      { source: "/why-rouvr", destination: "/#possibility", permanent: true },
      { source: "/capabilities", destination: "/#capabilities", permanent: true },
      { source: "/use-cases", destination: "/#pack", permanent: true },
      { source: "/who-its-for", destination: "/#pack", permanent: true },
      { source: "/security", destination: "/#trust", permanent: true },
      { source: "/about", destination: "/", permanent: true },
      { source: "/faq", destination: "/", permanent: true },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
        ],
      },
      {
        source: "/icon.svg",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=86400, stale-while-revalidate=604800",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
