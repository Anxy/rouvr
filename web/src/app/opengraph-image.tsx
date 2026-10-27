import { ImageResponse } from "next/og";

export const alt = "Rouvr. Make it happen. Your pack, your ambition.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 80,
          backgroundColor: "#071522",
          backgroundImage:
            "radial-gradient(ellipse at 20% 0%, rgba(8,169,240,0.35), transparent 60%)",
          color: "#F5F8FB",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 44, fontWeight: 700 }}>
          rouvr<span style={{ color: "#08A9F0" }}>.</span>
          <span
            style={{
              marginLeft: 20,
              fontSize: 18,
              letterSpacing: 4,
              color: "#6FD3FF",
              border: "1px solid rgba(8,169,240,0.4)",
              borderRadius: 999,
              padding: "6px 16px",
            }}
          >
            YOUR PACK
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ fontSize: 76, fontWeight: 700, lineHeight: 1.05, maxWidth: 960 }}>
            Rouvr. Make it happen.
          </div>
          <div style={{ fontSize: 28, color: "#8BA3B8" }}>
            Your pack, your ambition.
          </div>
        </div>
      </div>
    ),
    size,
  );
}
