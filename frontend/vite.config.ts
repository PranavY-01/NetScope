import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The UI never talks to the network itself: every request goes to the NetScope API. In development,
// the Vite server proxies /api to the backend so the browser stays same-origin (no CORS needed).
const backend = process.env.NETSCOPE_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
