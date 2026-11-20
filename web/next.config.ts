import type { NextConfig } from "next";

// Static export (architecture section 5.1). Server Components run at build time only.
// Security headers live in vercel.json because static exports cannot set headers.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
