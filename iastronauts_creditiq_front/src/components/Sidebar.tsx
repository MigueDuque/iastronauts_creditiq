import { Link, useLocation } from 'react-router-dom'
import Drawer from '@mui/material/Drawer'
import Box from '@mui/material/Box'

const DRAWER_WIDTH = 220
const HEADER_HEIGHT = 65

const NAV_ITEMS = [
  {
    label: 'Dashboard',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
    path: '/',
  },
  {
    label: 'Markets',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="20" x2="18" y2="10" />
        <line x1="12" y1="20" x2="12" y2="4" />
        <line x1="6" y1="20" x2="6" y2="14" />
      </svg>
    ),
    path: '/markets',
  },
  {
    label: 'Watchlist',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    ),
    path: '/watchlist',
  },
  {
    label: 'Alerts',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
    ),
    path: '/alerts',
  },
  {
    label: 'AI Analysis',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <polyline points="10 9 9 9 8 9" />
      </svg>
    ),
    path: '/analysis',
  },
  {
    label: 'Settings',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    ),
    path: '/settings',
  },
]

const paperSx = {
  width: DRAWER_WIDTH,
  boxSizing: 'border-box' as const,
  bgcolor: '#06091a',
  borderRight: '1px solid rgba(59,130,246,0.1)',
  top: HEADER_HEIGHT,
  height: `calc(100vh - ${HEADER_HEIGHT}px)`,
  display: 'flex',
  flexDirection: 'column',
}

interface SidebarProps {
  mobileOpen: boolean
  onClose: () => void
  onUploadClick: () => void
}

export default function Sidebar({ mobileOpen, onClose, onUploadClick }: SidebarProps) {
  const { pathname } = useLocation()

  const drawerContent = (
    <Box
      sx={{ display: 'flex', flexDirection: 'column', height: '100%', p: '16px 12px' }}
    >
      {/* Logo area */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 1, mb: 3 }}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: '8px',
            background: 'linear-gradient(135deg, #1d4ed8, #0ea5e9)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
            <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" opacity="0.9" />
          </svg>
        </Box>
        <Box>
          <p style={{ margin: 0, fontSize: 14, fontWeight: 800, color: '#f1f5f9', letterSpacing: '-0.01em' }}>
            CREDITIQ
          </p>
          <p style={{ margin: 0, fontSize: 10, color: '#475569', letterSpacing: '0.04em' }}>
            AI Financial Intelligence
          </p>
        </Box>
      </Box>

      {/* Nav items */}
      <nav style={{ flex: 1 }}>
        {NAV_ITEMS.map((item) => {
          // treat /markets, /watchlist, /alerts, /settings as placeholder (no real route)
          const isPlaceholder = !['/','analysis'].some(p => item.path === p || item.path === '/analysis')
          const active = pathname === item.path

          const content = (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                px: 1.5,
                py: 1.1,
                borderRadius: '8px',
                mb: 0.5,
                cursor: 'pointer',
                color: active ? '#f1f5f9' : '#64748b',
                background: active ? 'rgba(59,130,246,0.12)' : 'transparent',
                boxShadow: active ? 'inset 2px 0 0 #3b82f6' : 'none',
                transition: 'all 0.15s',
                '&:hover': {
                  color: '#e2e8f0',
                  background: 'rgba(255,255,255,0.04)',
                },
              }}
            >
              <Box sx={{ color: active ? '#38bdf8' : 'inherit', display: 'flex', flexShrink: 0 }}>
                {item.icon}
              </Box>
              <span
                style={{
                  fontSize: 13.5,
                  fontWeight: active ? 600 : 500,
                  letterSpacing: '-0.01em',
                }}
              >
                {item.label}
              </span>
            </Box>
          )

          if (isPlaceholder) {
            return <Box key={item.path}>{content}</Box>
          }

          return (
            <Link key={item.path} to={item.path} style={{ textDecoration: 'none' }} onClick={onClose}>
              {content}
            </Link>
          )
        })}
      </nav>

      {/* User profile */}
      <Box sx={{ borderTop: '1px solid rgba(59,130,246,0.1)', pt: 2 }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
            px: 1.5,
            py: 1,
            borderRadius: '8px',
            cursor: 'pointer',
            '&:hover': { background: 'rgba(255,255,255,0.04)' },
          }}
        >
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #1e40af, #0ea5e9)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 13,
              fontWeight: 700,
              color: '#fff',
              flexShrink: 0,
            }}
          >
            IA
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>Iastronauts</p>
            <p style={{ margin: 0, fontSize: 11, color: '#475569' }}>Pro Plan</p>
          </Box>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#475569" strokeWidth="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </Box>

        {/* Ask AI */}
        <button
          onClick={() => { onClose(); onUploadClick() }}
          style={{
            width: '100%',
            marginTop: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '9px 14px',
            borderRadius: 8,
            border: '1px solid rgba(59,130,246,0.2)',
            background: 'rgba(59,130,246,0.06)',
            color: '#94a3b8',
            fontSize: 13,
            fontWeight: 500,
            cursor: 'pointer',
            fontFamily: 'inherit',
            transition: 'all 0.15s',
          }}
          onMouseEnter={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.background = 'rgba(59,130,246,0.12)'
            ;(e.currentTarget as HTMLButtonElement).style.color = '#e2e8f0'
          }}
          onMouseLeave={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.background = 'rgba(59,130,246,0.06)'
            ;(e.currentTarget as HTMLButtonElement).style.color = '#94a3b8'
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style={{ color: '#38bdf8' }}>
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
          Ask AI anything
        </button>
      </Box>
    </Box>
  )

  return (
    <>
      {/* Desktop — permanent */}
      <Drawer
        variant="permanent"
        sx={{
          display: { xs: 'none', lg: 'block' },
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': paperSx,
        }}
      >
        {drawerContent}
      </Drawer>

      {/* Mobile — temporary */}
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={onClose}
        ModalProps={{ keepMounted: true }}
        sx={{
          display: { xs: 'block', lg: 'none' },
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            bgcolor: '#06091a',
            borderRight: '1px solid rgba(59,130,246,0.1)',
          },
        }}
      >
        {drawerContent}
      </Drawer>
    </>
  )
}
