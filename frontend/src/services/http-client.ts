function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  for (const part of document.cookie.split(';')) {
    const value = part.trim()
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length))
  }
  return null
}

let authenticatedRequests = new AbortController()

export function abortAuthenticatedRequests() {
  authenticatedRequests.abort()
  authenticatedRequests = new AbortController()
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = readCookie('media_pilot_csrf')
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(input, {
    ...init,
    credentials: 'same-origin',
    headers,
    signal: init.signal ?? authenticatedRequests.signal,
  })
  if (response.status === 401) window.dispatchEvent(new Event('media-pilot:unauthorized'))
  return response
}
