import { defineConfig } from "astro/config";

export default defineConfig({
  // Static demo build (scripts/freeze/build.sh) can live under a subpath, e.g. /demos/swarm-board/.
  base: process.env.FREEZE_BASE || undefined,
  server: {
    host: process.env.HOST || "127.0.0.1",
    port: 4322,
    allowedHosts: (process.env.ALLOWED_HOSTS || "").split(",").filter(Boolean),
  },
  vite: {
    server: {
      allowedHosts: (process.env.ALLOWED_HOSTS || "").split(",").filter(Boolean),
    },
  },
  devToolbar: {
    enabled: false,
  },
});
