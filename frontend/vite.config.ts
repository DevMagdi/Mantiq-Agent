import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: false,
    // The preview proxy terminates TLS on an external host, so allow any Host header.
    allowedHosts: true,
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000", ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Split the heavy chart/table/drag libraries away from the app shell so the
        // first paint stays small and the login screen loads fast on mobile networks.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
          tables: ["@tanstack/react-table", "@tanstack/react-virtual"],
          drag: ["@dnd-kit/core", "@dnd-kit/sortable"],
        },
      },
    },
  },
});
