import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Python API runs on :8001. Proxying keeps the browser on one origin.
const api = "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: api, rewrite: (p) => p.replace(/^\/api/, "") },
      "/files": api,
    },
  },
});
