import { defineConfig } from "vite";

// Proxies /api/* to the FastAPI dev server (see serve_cli.py) so the frontend
// can call relative /api/... paths without hardcoding a backend URL or
// relying on CORS during local dev (Implementation Plan.md section 11/12).
export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
