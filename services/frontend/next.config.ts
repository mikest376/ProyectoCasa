import type { NextConfig } from "next";

// La interfaz solo habla con su propio origen y, en desarrollo, con el puerto de la API.
const policy = [
  "default-src 'self'",
  "connect-src 'self' http://localhost:8000 http://127.0.0.1:8000",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  // Next.js inserta scripts en la página; sin nonce, hay que permitirlos.
  "script-src 'self' 'unsafe-inline'",
  "frame-ancestors 'self'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  // Imagen mínima: solo el servidor y sus dependencias reales.
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  headers: async () => [
    { source: "/:path*", headers: [{ key: "Content-Security-Policy", value: policy }] },
  ],
};

export default nextConfig;
