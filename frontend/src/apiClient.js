const RUNTIME_API_BASE_URL = typeof window !== 'undefined' ? window.__PAYER_PAYEE_API_URL__ : ''
const CONFIGURED_API_BASE_URL = (
  RUNTIME_API_BASE_URL
  || import.meta.env.VITE_API_BASE_URL
  || ''
).replace(/\/$/, '')
const LOCAL_API_BASE_URL = 'http://127.0.0.1:4000'
const AUTH_STATUSES = new Set([401, 403, 413, 429])

let csrfToken = ''
let onUnauthorized = () => {}

export function setApiSession({ csrf = '', unauthorized } = {}) {
  csrfToken = csrf
  if (typeof unauthorized === 'function') onUnauthorized = unauthorized
}

export function apiBaseUrl() {
  return CONFIGURED_API_BASE_URL
}

export async function apiFetchJson(path, options = {}) {
  const candidates = [...new Set([
    CONFIGURED_API_BASE_URL,
    window.location.origin,
    import.meta.env.DEV ? LOCAL_API_BASE_URL : '',
  ].filter(Boolean))]
  let lastError = new Error('Backend API could not be reached')
  const method = (options.method || 'GET').toUpperCase()
  const headers = { Accept: 'application/json', ...(options.headers || {}) }
  if (!['GET', 'HEAD'].includes(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken

  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}${path}`, {
        ...options,
        credentials: 'include',
        headers,
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        const error = new Error(payload?.message || `Request failed: ${response.status}`)
        error.status = response.status
        if (response.status === 401) onUnauthorized()
        // A failed write may already have committed. Never replay it against
        // another server; only read requests may use a fallback origin.
        if (!['GET', 'HEAD'].includes(method) || ![404, 405, 502, 504].includes(response.status)) throw error
        lastError = error
        continue
      }
      return await response.json()
    } catch (error) {
      if (!['GET', 'HEAD'].includes(method)) throw error
      if (AUTH_STATUSES.has(error.status) || !['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.'].includes(error.message)) {
        throw error
      }
      lastError = error
    }
  }
  throw lastError
}
