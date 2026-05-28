import { Link, useLocation } from 'react-router-dom'
import Drawer from '@mui/material/Drawer'
import List from '@mui/material/List'
import ListItemButton from '@mui/material/ListItemButton'
import ListItemIcon from '@mui/material/ListItemIcon'
import ListItemText from '@mui/material/ListItemText'
import Divider from '@mui/material/Divider'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'

const DRAWER_WIDTH = 240
const HEADER_HEIGHT = 65

const ICONS = '/icons'

const navItems = [
  { label: 'Executive Overview', icon: 'dashboard', path: '/' },
  { label: 'AI Analysis', icon: 'psychology', path: '/analysis' },
]

const paperSx = {
  width: DRAWER_WIDTH,
  boxSizing: 'border-box' as const,
  bgcolor: '#050816',
  borderRight: '1px solid rgba(47, 128, 255, 0.16)',
  boxShadow: '4px 0 24px rgba(47, 128, 255, 0.06)',
  top: HEADER_HEIGHT,
  height: `calc(100vh - ${HEADER_HEIGHT}px)`,
}

interface SidebarProps {
  mobileOpen: boolean
  onClose: () => void
  onUploadClick: () => void
}

export default function Sidebar({ mobileOpen, onClose, onUploadClick }: SidebarProps) {
  const { pathname } = useLocation()

  const drawerContent = (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Logo */}
      <Box sx={{ px: 2, py: 3, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
        <img
          src={`${ICONS}/icono.png`}
          alt="CreditIQ"
          style={{ height: 60, objectFit: 'contain' }}
        />
      </Box>

      {/* Section label */}
      <Box sx={{ px: 2, mb: 1 }}>
        <span style={{
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: 10,
          letterSpacing: '0.05em',
          color: '#94A3B8',
          textTransform: 'uppercase',
        }}>Core Modules</span>
      </Box>

      {/* Nav items */}
      <List sx={{ px: 1, flex: 1 }} disablePadding>
        {navItems.map((item) => {
          const active = pathname === item.path
          return (
            <ListItemButton
              key={item.path}
              component={Link}
              to={item.path}
              onClick={onClose}
              selected={active}
              sx={{
                borderRadius: '8px',
                mb: 0.5,
                color: active ? '#56CCF2' : '#94A3B8',
                '&.Mui-selected': {
                  bgcolor: '#111830',
                  color: '#56CCF2',
                  boxShadow: '0 0 20px rgba(47, 128, 255, 0.18)',
                  '&:hover': { bgcolor: '#111830' },
                },
                '&:hover': { bgcolor: '#0d1228', color: '#F5F7FA' },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36, color: 'inherit' }}>
                <span
                  className="material-symbols-outlined"
                  style={{
                    fontSize: 20,
                    fontVariationSettings: active ? "'FILL' 1" : "'FILL' 0",
                  }}
                >
                  {item.icon}
                </span>
              </ListItemIcon>
              <ListItemText
                primary={item.label}
                slotProps={{
                  primary: {
                    style: {
                      fontFamily: 'Geist, sans-serif',
                      fontSize: 13,
                      fontWeight: active ? 600 : 500,
                      letterSpacing: '-0.01em',
                    },
                  },
                }}
              />
            </ListItemButton>
          )
        })}
      </List>

      {/* Bottom section */}
      <Divider sx={{ borderColor: 'rgba(47, 128, 255, 0.14)', mx: 1 }} />
      <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Button
          fullWidth
          onClick={() => { onClose(); onUploadClick() }}
          startIcon={<span className="material-symbols-outlined" style={{ fontSize: 18 }}>upload_file</span>}
          sx={{
            bgcolor: '#2F80FF',
            color: '#F5F7FA',
            borderRadius: '8px',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 12,
            letterSpacing: '0.05em',
            textTransform: 'none',
            fontWeight: 500,
            py: 1.25,
            boxShadow: '0 0 20px rgba(47, 128, 255, 0.30)',
            '&:hover': { bgcolor: '#2F80FF', opacity: 0.88, boxShadow: '0 0 30px rgba(47, 128, 255, 0.45)' },
          }}
        >
          Upload Document
        </Button>

        {/* System status */}
        <Box
          sx={{
            bgcolor: '#0d1228',
            border: '1px solid rgba(47, 128, 255, 0.16)',
            borderRadius: '8px',
            p: 1.5,
          }}
        >
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#94A3B8', letterSpacing: '0.05em' }}>
              SYSTEM STATUS
            </span>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box
                sx={{
                  width: 7, height: 7, borderRadius: '50%', bgcolor: '#56F2C1',
                  animation: 'pulse 2s infinite',
                  boxShadow: '0 0 8px rgba(86, 242, 193, 0.6)',
                  '@keyframes pulse': {
                    '0%, 100%': { opacity: 1 },
                    '50%': { opacity: 0.4 },
                  },
                }}
              />
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#56F2C1', letterSpacing: '0.05em' }}>Online</span>
            </Box>
          </Box>
          <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#94A3B8', letterSpacing: '0.05em' }}>
            Last model sync: 2m ago
          </span>
        </Box>
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
            bgcolor: '#050816',
            borderRight: '1px solid rgba(47, 128, 255, 0.16)',
          },
        }}
      >
        {drawerContent}
      </Drawer>
    </>
  )
}
