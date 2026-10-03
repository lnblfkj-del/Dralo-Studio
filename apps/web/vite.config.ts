import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: {
    "@edition": fileURLToPath(new URL("./src/editions/standalone.tsx", import.meta.url)),
    "@": fileURLToPath(new URL("./src", import.meta.url)),
  } },
  server: {
    host: "127.0.0.1", port: 5173, strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { outDir: "dist-standalone", sourcemap: false, manifest: true },
});
