import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function LoginPage() {
  const navigate = useNavigate()
  const { email, signIn } = useAuth()
  const [emailInput, setEmailInput] = useState('')
  const [password, setPassword]     = useState('')
  const [error, setError]           = useState<string | null>(null)
  const [loading, setLoading]       = useState(false)

  // Already authenticated — go straight to app
  if (email) {
    navigate('/', { replace: true })
    return null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await signIn(emailInput, password)
      navigate('/', { replace: true })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error al iniciar sesión')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: 'var(--color-surface-deep)',
      padding: '24px',
    }}>
      <div style={{
        width: '100%',
        maxWidth: 420,
        backgroundColor: '#0b0f24',
        border: '1px solid rgba(47, 128, 255, 0.18)',
        borderRadius: 16,
        padding: '40px 36px',
        boxShadow: '0 0 60px rgba(47, 128, 255, 0.08)',
      }}>
        {/* Logo / title */}
        <div style={{ marginBottom: 32, textAlign: 'center' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: '#F5F7FA', letterSpacing: '-0.5px' }}>
            Credit<span style={{ color: '#2F80FF' }}>IQ</span>
          </div>
          <div style={{ fontSize: 14, color: '#64748B', marginTop: 6 }}>
            Plataforma de Análisis Financiero
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 13, color: '#94A3B8', marginBottom: 6 }}>
              Correo electrónico
            </label>
            <input
              type="email"
              value={emailInput}
              onChange={e => setEmailInput(e.target.value)}
              required
              autoComplete="email"
              placeholder="usuario@empresa.com"
              style={{
                width: '100%',
                padding: '10px 14px',
                backgroundColor: '#111830',
                border: '1px solid rgba(47, 128, 255, 0.22)',
                borderRadius: 8,
                color: '#F5F7FA',
                fontSize: 14,
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <label style={{ display: 'block', fontSize: 13, color: '#94A3B8', marginBottom: 6 }}>
              Contraseña
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              placeholder="••••••••"
              style={{
                width: '100%',
                padding: '10px 14px',
                backgroundColor: '#111830',
                border: '1px solid rgba(47, 128, 255, 0.22)',
                borderRadius: 8,
                color: '#F5F7FA',
                fontSize: 14,
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {error && (
            <div style={{
              marginBottom: 16,
              padding: '10px 14px',
              backgroundColor: 'rgba(220, 38, 38, 0.12)',
              border: '1px solid rgba(220, 38, 38, 0.28)',
              borderRadius: 8,
              color: '#FCA5A5',
              fontSize: 13,
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '11px 0',
              backgroundColor: loading ? '#1a2a4a' : '#2F80FF',
              border: 'none',
              borderRadius: 8,
              color: '#F5F7FA',
              fontSize: 15,
              fontWeight: 600,
              cursor: loading ? 'default' : 'pointer',
              transition: 'background 0.2s',
            }}
          >
            {loading ? 'Iniciando sesión…' : 'Iniciar sesión'}
          </button>
        </form>
      </div>
    </div>
  )
}
