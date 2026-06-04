/**
 * Cognito / Amplify auth initialisation.
 *
 * When VITE_COGNITO_USER_POOL_ID is not set (local dev against local_server.py)
 * Amplify is not configured and all auth functions return safe no-op values.
 * API calls fall back to the x-tenant-id header so local_server.py still works.
 */

import { Amplify } from 'aws-amplify'
import {
  signIn as _signIn,
  signOut as _signOut,
  fetchAuthSession,
  getCurrentUser,
  type AuthUser,
} from 'aws-amplify/auth'

const USER_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID as string | undefined
const CLIENT_ID    = import.meta.env.VITE_COGNITO_CLIENT_ID    as string | undefined

export const cognitoEnabled = Boolean(USER_POOL_ID && CLIENT_ID)

if (cognitoEnabled) {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: USER_POOL_ID!,
        userPoolClientId: CLIENT_ID!,
        loginWith: { email: true },
      },
    },
  })
}

export async function getIdToken(): Promise<string | null> {
  if (!cognitoEnabled) return null
  try {
    const session = await fetchAuthSession()
    return session.tokens?.idToken?.toString() ?? null
  } catch {
    return null
  }
}

export async function getAuthUser(): Promise<AuthUser | null> {
  if (!cognitoEnabled) return null
  try {
    return await getCurrentUser()
  } catch {
    return null
  }
}

export async function login(email: string, password: string): Promise<void> {
  await _signIn({ username: email, password })
}

export async function logout(): Promise<void> {
  if (!cognitoEnabled) return
  await _signOut()
}
