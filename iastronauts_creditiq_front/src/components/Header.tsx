import IconButton from '@mui/material/IconButton'
import Tooltip from '@mui/material/Tooltip'
import { useAuth } from '../contexts/AuthContext'
import { cognitoEnabled } from '../lib/auth'

interface HeaderProps {
  onMenuToggle?: () => void
}

export default function Header({ onMenuToggle }: HeaderProps) {
  const { email, signOut } = useAuth()

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 1200,
        backgroundColor: 'rgba(5, 8, 22, 0.72)',
        backdropFilter: 'blur(14px)',
        WebkitBackdropFilter: 'blur(14px)',
        borderBottom: '1px solid rgba(47, 128, 255, 0.16)',
        boxShadow: '0 0 30px rgba(47, 128, 255, 0.08)',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          width: '100%',
          maxWidth: 1440,
          margin: '0 auto',
          padding: '0 32px',
          height: 65,
        }}
      >
        {/* Left: hamburger (mobile) + logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {onMenuToggle && (
            <IconButton
              onClick={onMenuToggle}
              sx={{
                display: { xs: 'flex', lg: 'none' },
                color: '#94A3B8',
                '&:hover': { color: '#F5F7FA', bgcolor: '#0d1228' },
              }}
            >
              <span className="material-symbols-outlined">menu</span>
            </IconButton>
          )}
        </div>

        {/* Right: action icons + user */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {['search', 'help', 'language'].map((icon) => (
            <IconButton
              key={icon}
              sx={{ color: '#94A3B8', '&:hover': { color: '#56CCF2', bgcolor: '#0d1228' } }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 20 }}>{icon}</span>
            </IconButton>
          ))}

          {/* User chip */}
          {cognitoEnabled && email && email !== 'local' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8 }}>
              <span style={{ fontSize: 12, color: '#64748B', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {email}
              </span>
              <Tooltip title="Cerrar sesión">
                <IconButton
                  onClick={signOut}
                  size="small"
                  sx={{ color: '#64748B', '&:hover': { color: '#FCA5A5', bgcolor: '#1a0a0a' } }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 18 }}>logout</span>
                </IconButton>
              </Tooltip>
            </div>
          )}

          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              backgroundColor: '#111830',
              border: '1px solid rgba(47, 128, 255, 0.20)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginLeft: 8,
              boxShadow: '0 0 12px rgba(47, 128, 255, 0.10)',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18, color: '#94A3B8' }}>person</span>
          </div>
        </div>
      </div>
    </header>
  )
}
