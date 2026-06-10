/**
 * Authenticated fetch wrapper.
 *
 * In production (Cognito enabled): adds "Authorization: Bearer <id_token>".
 * In local dev (no Cognito): adds "x-tenant-id: demo" so local_server.py works.
 *
 * Token refresh is handled transparently by Amplify's fetchAuthSession().
 */

import { cognitoEnabled, getIdToken } from './auth'

async function authHeaders(): Promise<Record<string, string>> {
  if (!cognitoEnabled) {
    const devTenant = import.meta.env.VITE_DEV_TENANT_ID || 'demo'
    return { 'x-tenant-id': devTenant }
  }
  const token = await getIdToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function apiFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const auth = await authHeaders()
  return fetch(url, {
    ...options,
    headers: {
      ...auth,
      ...(options.headers as Record<string, string> | undefined),
    },
  })
}
