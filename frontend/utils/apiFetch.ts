/** API calls with session cookie; SSR uses direct FastAPI URL (Vite proxy is browser-only). */

function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`
  if (import.meta.client) {
    return p
  }
  const cfg = useRuntimeConfig()
  const base = cfg.public.apiBase
  if (base != null && String(base).trim() !== "") {
    return `${String(base).replace(/\/$/, "")}${p}`
  }
  return `http://127.0.0.1:8090${p}`
}

export function apiFetch<T>(
  url: string,
  opts?: Parameters<typeof $fetch<T>>[1],
): Promise<T> {
  return $fetch<T>(apiUrl(url), { credentials: "include", ...opts })
}
