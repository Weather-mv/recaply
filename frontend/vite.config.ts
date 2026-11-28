import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// Built into ../static/studio and served by Flask under /studio (see studio_app.py).
// `npm run dev` proxies the API to a running Clipforge (python app.py) on :5000.
export default defineConfig({
  base: "/static/studio/",
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: { outDir: "../static/studio", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:5000", "/clipjob": "http://127.0.0.1:5000", "/static/fonts": "http://127.0.0.1:5000" },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
