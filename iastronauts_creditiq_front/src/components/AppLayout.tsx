import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import Box from '@mui/material/Box'
import Header from './Header'
import Sidebar from './Sidebar'
import Footer from './Footer'
import UploadDialog from './UploadDialog'

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', bgcolor: 'var(--color-surface-deep)' }}>
      <Header onMenuToggle={() => setMobileOpen((v) => !v)} />

      <Box sx={{ display: 'flex', flex: 1 }}>
        <Sidebar
          mobileOpen={mobileOpen}
          onClose={() => setMobileOpen(false)}
          onUploadClick={() => setUploadOpen(true)}
        />

        <Box
          component="main"
          sx={{
            flex: 1,
            minWidth: 0,
          }}
        >
          {/* Cap + center content so it stays aligned on wide screens / projectors
              instead of stretching tables edge-to-edge. */}
          <Box sx={{ width: '100%', maxWidth: 1440, mx: 'auto' }}>
            <Outlet />
          </Box>
        </Box>
      </Box>

      <Footer />

      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} />
    </Box>
  )
}
