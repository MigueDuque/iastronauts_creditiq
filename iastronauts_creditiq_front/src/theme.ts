import { createTheme } from '@mui/material/styles'

// CreditIQ Minimalist Blue Theme
// Soft blue gradients with glassmorphism

export const creditiqTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#3B8EFF',      // soft electric blue
      light: '#5BA4FF',     // lighter blue
      dark: '#2F80FF',      // deeper blue
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#5BB8FF',      // soft cyan
      light: '#7FD0FF',     // light cyan
      dark: '#3B8EFF',
      contrastText: '#FFFFFF',
    },
    success: {
      main: '#6DD4FF',      // blue-tinted success
      contrastText: '#0A0F1C',
    },
    warning: {
      main: '#FFB84D',      // soft orange
      contrastText: '#0A0F1C',
    },
    error: {
      main: '#FF6B9D',      // soft pink-red
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#0A0F1C',   // dark blue
      paper: 'rgba(20, 35, 60, 0.4)', // semi-transparent
    },
    text: {
      primary: '#FFFFFF',   // pure white
      secondary: '#A8B8D8', // soft blue-gray
    },
    divider: 'rgba(59, 142, 255, 0.15)',
  },
  typography: {
    fontFamily: "'Geist', 'Inter', sans-serif",
    h1: {
      fontFamily: "'Geist', sans-serif",
      fontWeight: 700,
    },
    h2: {
      fontFamily: "'Geist', sans-serif",
      fontWeight: 700,
    },
    h3: {
      fontFamily: "'Geist', sans-serif",
      fontWeight: 600,
    },
    h4: {
      fontFamily: "'Geist', sans-serif",
      fontWeight: 600,
    },
    body1: {
      fontFamily: "'Geist', sans-serif",
    },
    body2: {
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: '0.875rem',
    },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          background: 'linear-gradient(180deg, #0A0F1C 0%, #0F1829 50%, #0A1120 100%)',
          backgroundAttachment: 'fixed',
          color: '#FFFFFF',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 12,
          fontFamily: "'Geist', sans-serif",
          fontSize: '0.875rem',
          fontWeight: 500,
          padding: '10px 24px',
        },
        contained: {
          background: 'linear-gradient(135deg, #3B8EFF, #5BB8FF)',
          boxShadow: '0 4px 16px rgba(59, 142, 255, 0.3)',
          '&:hover': {
            background: 'linear-gradient(135deg, #5BA4FF, #7FD0FF)',
            boxShadow: '0 6px 24px rgba(59, 142, 255, 0.5)',
          },
        },
        outlined: {
          border: '1px solid rgba(59, 142, 255, 0.3)',
          color: '#5BA4FF',
          '&:hover': {
            background: 'rgba(59, 142, 255, 0.1)',
            borderColor: '#5BA4FF',
          },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          background: 'linear-gradient(135deg, rgba(20, 35, 60, 0.3), rgba(30, 50, 80, 0.25))',
          backdropFilter: 'blur(20px)',
          border: '1px solid rgba(59, 142, 255, 0.15)',
          borderRadius: 16,
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          background: 'linear-gradient(180deg, rgba(15, 24, 41, 0.8) 0%, rgba(10, 15, 28, 0.9) 100%)',
          backdropFilter: 'blur(20px)',
          borderRight: '1px solid rgba(59, 142, 255, 0.2)',
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          '&.Mui-selected': {
            background: 'linear-gradient(135deg, rgba(59, 142, 255, 0.2), rgba(91, 180, 255, 0.15))',
            boxShadow: '0 0 24px rgba(59, 142, 255, 0.4)',
            '&:hover': {
              background: 'linear-gradient(135deg, rgba(59, 142, 255, 0.3), rgba(91, 180, 255, 0.25))',
            },
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderColor: 'rgba(59, 142, 255, 0.1)',
        },
        head: {
          background: 'linear-gradient(135deg, rgba(20, 35, 60, 0.5), rgba(30, 50, 80, 0.4))',
          color: '#A8B8D8',
          fontFamily: "'Geist', sans-serif",
          fontSize: '0.75rem',
          fontWeight: 600,
          letterSpacing: '0.05em',
          textTransform: 'uppercase',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontFamily: "'Geist', sans-serif",
          fontSize: '0.75rem',
          borderRadius: 8,
        },
      },
    },
  },
})

// Export color constants for use in components
export const CREDITIQ_COLORS = {
  // Primary palette - Minimalist Blue
  darkBackground: '#0A0F1C',
  surfaceBlue: 'rgba(20, 35, 60, 0.4)',
  electricBlue: '#3B8EFF',
  softBlue: '#5BA4FF',
  lightBlue: '#A8CEFF',
  cyanGlow: '#5BB8FF',
  lightCyan: '#7FD0FF',
  blueCyan: '#6DD4FF',
  pureWhite: '#FFFFFF',
  blueGray: '#A8B8D8',
  softOrange: '#FFB84D',
  softPink: '#FF6B9D',
  
  // Semantic
  background: '#0A0F1C',
  surface: 'rgba(20, 35, 60, 0.4)',
  primary: '#3B8EFF',
  primaryLight: '#5BA4FF',
  secondary: '#5BB8FF',
  success: '#6DD4FF',
  warning: '#FFB84D',
  danger: '#FF6B9D',
  textPrimary: '#FFFFFF',
  textSecondary: '#A8B8D8',
  border: 'rgba(59, 142, 255, 0.15)',
  
  // Category colors
  assets: '#5BA4FF',
  liabilities: '#FF6B9D',
  equity: '#6DD4FF',
  revenue: '#FFB84D',
  expense: '#7FD0FF',
  other: '#A8B8D8',
  
  // Risk levels
  riskLow: '#6DD4FF',
  riskMedium: '#FFB84D',
  riskHigh: '#FF6B9D',
  
  // Health status
  stable: '#6DD4FF',
  growing: '#5BA4FF',
  declining: '#FFB84D',
  critical: '#FF6B9D',
}
