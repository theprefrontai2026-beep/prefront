import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Verdict is mostly a standalone app: the orchestrator calls
// (/api/scenarios, /api/run) are plain cross-origin fetches at an absolute
// URL, and that service already sends permissive CORS headers. The
// exceptions are SessionDetail's oob-ingest calls (/oob/sessions/:id) and
// eval-engine's verdict/conformance calls (/eval/sessions/:id/*), both
// inherited from the main app's Observability.tsx — those need a
// same-origin proxy, mirrored here for dev and in verdict-nginx.conf for
// the built container.
const OOB_TARGET = process.env.VITE_OOB_TARGET || "http://localhost:8110";
const EVAL_TARGET = process.env.VITE_EVAL_TARGET || "http://localhost:8120";

const rawPort = process.env.PORT;

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const basePath = process.env.BASE_PATH;

if (!basePath) {
  throw new Error(
    "BASE_PATH environment variable is required but was not provided.",
  );
}

export default defineConfig({
  base: basePath,
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
    dedupe: ["react", "react-dom"],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist"),
    emptyOutDir: true,
  },
  server: {
    port,
    strictPort: true,
    host: "0.0.0.0",
    allowedHosts: true,
    fs: {
      strict: true,
    },
    proxy: {
      "/oob": { target: OOB_TARGET, changeOrigin: true },
      "/eval": { target: EVAL_TARGET, changeOrigin: true },
    },
  },
  preview: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
  },
});
