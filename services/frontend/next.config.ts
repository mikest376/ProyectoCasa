import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Imagen mínima: solo el servidor y sus dependencias reales.
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
