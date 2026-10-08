import type { NextConfig } from "next";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Kept out of the bundle: the ffmpeg installer resolves a platform binary at runtime, and opentype.js
  // only exposes its default export in the CommonJS build (Turbopack would pick the ESM build and fail).
  serverExternalPackages: ["pg", "@ffmpeg-installer/ffmpeg", "opentype.js"],
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
