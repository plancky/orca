import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  server: {
    // Google's OAuth callback is registered at the SPA origin (:5173); forward
    // /api/v1 to the backend so that browser redirect reaches FastAPI. The SPA's
    // own API calls use the absolute VITE_API_BASE_URL and bypass this proxy.
    proxy: {
      "/api/v1": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  resolve: {
    // Single React + Query instance across the app and pre-bundled deps,
    // otherwise openapi-react-query's useQueryClient sees a null context.
    dedupe: ["react", "react-dom", "@tanstack/react-query"],
    alias: {
      // shadcn-generated imports use "~/..." — must match tsconfig paths.
      "~": fileURLToPath(new URL("./app", import.meta.url)),
    },
  },
  optimizeDeps: {
    include: [
      "react",
      "react-dom",
      "@tanstack/react-query",
      "openapi-fetch",
      "openapi-react-query",
      "lucide-react",
      "sonner",
      "next-themes",
      "radix-ui",
    ],
  },
  build: {
    sourcemap: false,
  },
});
