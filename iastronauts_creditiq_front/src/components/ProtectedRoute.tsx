import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { cognitoEnabled } from '../lib/auth'

interface Props {
  children: React.ReactNode
}

export default function ProtectedRoute({ children }: Props) {
  const { loading, email } = useAuth()

  // Cognito not configured → local dev, always allow through
  if (!cognitoEnabled) return <>{children}</>

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--color-surface-deep)',
        color: '#64748B',
        fontSize: 14,
      }}>
        Cargando…
      </div>
    )
  }

  if (!email) return <Navigate to="/login" replace />

  return <>{children}</>
}
