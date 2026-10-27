import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/utils";

/** Stable last-modified dates — bump when the page content materially changes. */
const routes: {
  path: string;
  priority: number;
  changeFrequency: MetadataRoute.Sitemap[number]["changeFrequency"];
  lastModified: string;
}[] = [
  { path: "", priority: 1, changeFrequency: "weekly", lastModified: "2026-10-16" },
  { path: "/contact", priority: 0.8, changeFrequency: "monthly", lastModified: "2026-10-16" },
  { path: "/privacy", priority: 0.3, changeFrequency: "yearly", lastModified: "2026-10-16" },
  { path: "/terms", priority: 0.3, changeFrequency: "yearly", lastModified: "2026-10-16" },
];

export default function sitemap(): MetadataRoute.Sitemap {
  return routes.map((route) => ({
    url: `${SITE_URL}${route.path}`,
    lastModified: new Date(route.lastModified),
    changeFrequency: route.changeFrequency,
    priority: route.priority,
  }));
}
