import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["cueso-icon-192.png", "cueso-icon-512.png"],
        manifest: {
          name: "Cueso",
          short_name: "Cueso",
          description: "Voice-controlled Roku via AI",
          theme_color: "#0a0a0f",
          background_color: "#0a0a0f",
          display: "standalone",
          orientation: "portrait",
          start_url: "/",
          icons: [
            {
              src: "cueso-icon-192.png",
              sizes: "192x192",
              type: "image/png",
            },
            {
              src: "cueso-icon-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "any maskable",
            },
          ],
        },
        workbox: {
          globPatterns: ["**/*.{js,css,html,ico,png,svg}"],
        },
      }),
    ],
    server: {
      port: Number(env.VITE_PORT) || 8484,
      allowedHosts: env.VITE_ALLOWED_HOSTS ? env.VITE_ALLOWED_HOSTS.split(",") : [],
    },
  };
});