/** Redirect to /login when dashboard password is configured and session is missing. */
export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === "/login") return

  try {
    const status = await apiFetch<{ authRequired: boolean; authenticated: boolean }>(
      "/api/auth/status",
    )
    if (status.authRequired && !status.authenticated) {
      return navigateTo({ path: "/login", query: { redirect: to.fullPath } })
    }
  } catch {
    // API down or not started — do not block the UI (avoid redirect loop)
    return
  }
})
