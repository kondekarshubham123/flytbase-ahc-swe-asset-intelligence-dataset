import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on :8077 during development; /api is proxied to it.
// Set COPILOT_API to point at a different backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.COPILOT_API || "http://127.0.0.1:8077",
        changeOrigin: true,
      },
    },
  },
});
