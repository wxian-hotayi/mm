import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The frontend talks only to the backend's `/api` surface (DESIGN §2). In dev
// the Vite server proxies `/api` to the FastAPI app on :8000 with
// `changeOrigin` and cookie pass-through so the HttpOnly `wos_access` /
// `wos_refresh` session cookies (DESIGN §20.8) round-trip transparently.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // Preserve the cookie path so SameSite=Lax session cookies stick.
        cookieDomainRewrite: "localhost",
        cookiePathRewrite: { "/api/v1/auth": "/api/v1/auth", "/": "/" },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
