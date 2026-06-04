import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { cognitoEnabled, getAuthUser, login, logout } from '../lib/auth'

interface AuthState {
  loading: boolean
  email: string | null
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  loading: true,
  email: null,
  signIn: async () => {},
  signOut: async () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(cognitoEnabled)
  const [email, setEmail] = useState<string | null>(cognitoEnabled ? null : 'local')

  useEffect(() => {
    if (!cognitoEnabled) return
    getAuthUser()
      .then(user => setEmail(user?.signInDetails?.loginId ?? null))
      .finally(() => setLoading(false))
  }, [])

  async function signIn(emailAddr: string, password: string) {
    await login(emailAddr, password)
    const user = await getAuthUser()
    setEmail(user?.signInDetails?.loginId ?? null)
  }

  async function signOut() {
    await logout()
    setEmail(null)
  }

  return (
    <AuthContext.Provider value={{ loading, email, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}
