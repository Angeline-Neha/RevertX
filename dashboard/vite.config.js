import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/workflow": "http://localhost:8000",
    },
  },
});
