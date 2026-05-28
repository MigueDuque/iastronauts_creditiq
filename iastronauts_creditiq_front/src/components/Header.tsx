
import { Link, useLocation } from 'react-router-dom'
import IconButton from '@mui/material/IconButton'

interface HeaderProps {
  onMenuToggle?: () => void
}

export default function Header({ onMenuToggle }: HeaderProps) {
  const { pathname } = useLocation()

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 1200,
        backgroundColor: '#050816',
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

        {/* Center: nav tabs (desktop only, hidden when sidebar is present) */}
        <nav
          style={{
            display: 'flex',
            gap: 24,
            alignItems: 'center',
          }}
          className="hidden md:flex"
        >
          {[
            { path: '/', label: 'Dashboard', icon: 'dashboard' },
            { path: '/analysis', label: 'Analyses', icon: 'analytics' },
          ].map(({ path, label, icon }) => {
            const active = pathname === path
            return (
              <Link
                key={path}
                to={path}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  paddingBottom: 4,
                  textDecoration: 'none',
                  color: active ? '#2F80FF' : '#94A3B8',
                  fontWeight: active ? 700 : 500,
                  borderBottom: active ? '2px solid #2F80FF' : '2px solid transparent',
                  transition: 'color 0.2s, border-color 0.2s',
                }}
              >
                <span
                  className="material-symbols-outlined"
                  style={{
                    fontSize: 18,
                    fontVariationSettings: active ? "'FILL' 1" : "'FILL' 0",
                  }}
                >
                  {icon}
                </span>
                <span
                  style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: 12,
                    letterSpacing: '0.05em',
                    fontWeight: 500,
                  }}
                >
                  {label}
                </span>
              </Link>
            )
          })}
        </nav>

        {/* Right: action icons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {['search', 'help', 'language'].map((icon) => (
            <IconButton
              key={icon}
              sx={{ color: '#94A3B8', '&:hover': { color: '#56CCF2', bgcolor: '#0d1228' } }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 20 }}>{icon}</span>
            </IconButton>
          ))}
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
