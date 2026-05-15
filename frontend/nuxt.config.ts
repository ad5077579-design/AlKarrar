// https://nuxt.com/docs/api/configuration/nuxt-config
//
// Dev: browser calls same origin `/api/...` → Vite proxies to FastAPI (avoids cross-origin "Failed to fetch").
// Override with NUXT_PUBLIC_API_BASE / NUXT_PUBLIC_WS_URL when the API is on another host.
export default defineNuxtConfig({
  compatibilityDate: "2024-11-01",
  devtools: { enabled: true },
  modules: ["@pinia/nuxt"],
  css: ["~/assets/main.css"],
  nitro: {
    devProxy: {
      "/api": { target: "http://127.0.0.1:8090", changeOrigin: true },
      "/ws": { target: "http://127.0.0.1:8090", ws: true, changeOrigin: true },
    },
  },
  vite: {
    server: {
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8090",
          changeOrigin: true,
        },
        // Browser uses same-origin ``ws://localhost:<nuxt>/ws`` → FastAPI ``/ws`` (avoids cross-port issues).
        "/ws": {
          target: "http://127.0.0.1:8090",
          ws: true,
          changeOrigin: true,
        },
      },
    },
  },
  runtimeConfig: {
    public: {
      apiBase: "",
      // Empty = same-origin WebSocket (Vite proxy ``/ws`` in dev). Override with NUXT_PUBLIC_WS_URL for remote API.
      wsUrl: "",
      botId: "default",
    },
  },
})
