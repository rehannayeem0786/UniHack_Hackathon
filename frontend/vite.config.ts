import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    assetsDir: "assets",
    // Chunking is left to Rollup. Hand-splitting React into its own chunk
    // produced an empty stub, because every other chunk depends on it and the
    // shared code got hoisted elsewhere — the app then loaded but never
    // mounted. Code splitting is done in the app instead, by lazy-loading the
    // tab panels, which keeps the charting runtime out of the initial payload
    // without second-guessing the module graph.
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    // Dev server proxies the API so the browser sees one origin and CORS
    // never enters the picture during development.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/docs": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
