/** API calls with session cookie (dashboard login). */
export function apiFetch<T>(
  url: string,
  opts?: Parameters<typeof $fetch<T>>[1],
): Promise<T> {
  return $fetch<T>(url, { credentials: "include", ...opts })
}
