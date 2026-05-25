import { Link, useLocation } from 'react-router-dom'

export default function Header() {
  const { pathname } = useLocation()

  return (
    <header className="bg-background sticky top-0 z-50 border-b border-border">
      <div className="flex justify-between items-center w-full px-margin-mobile md:px-margin-desktop py-4 max-w-container-max-width mx-auto">
        {/* Brand */}
        <div className="flex items-center gap-4">
          <img alt="CreditIQ Logo" className="h-8 w-8 rounded-sm object-contain" src="/favicon.svg" />
          <h1 className="text-headline-md font-headline-md font-bold tracking-tight text-on-surface">CreditIQ</h1>
        </div>

        {/* Nav tabs */}
        <nav className="hidden md:flex gap-6 items-center">
          <Link
            to="/"
            className={`flex items-center gap-2 pb-1 transition-colors duration-200 ${
              pathname === '/'
                ? 'text-primary font-bold border-b-2 border-primary'
                : 'text-on-surface-variant font-medium hover:text-primary'
            }`}
          >
            <span className="material-symbols-outlined" style={{ fontVariationSettings: pathname === '/' ? "'FILL' 1" : "'FILL' 0" }}>
              dashboard
            </span>
            <span className="text-label-md font-label-md">Dashboard</span>
          </Link>
          <Link
            to="/analysis"
            className={`flex items-center gap-2 pb-1 transition-colors duration-200 ${
              pathname === '/analysis'
                ? 'text-primary font-bold border-b-2 border-primary'
                : 'text-on-surface-variant font-medium hover:text-primary'
            }`}
          >
            <span className="material-symbols-outlined" style={{ fontVariationSettings: pathname === '/analysis' ? "'FILL' 1" : "'FILL' 0" }}>
              analytics
            </span>
            <span className="text-label-md font-label-md">Analyses</span>
          </Link>
        </nav>

        {/* Actions */}
        <div className="flex items-center gap-4">
          <button className="text-on-surface-variant hover:text-primary transition-colors duration-200">
            <span className="material-symbols-outlined">search</span>
          </button>
          <button className="text-on-surface-variant hover:text-primary transition-colors duration-200">
            <span className="material-symbols-outlined">help</span>
          </button>
          <button className="text-on-surface-variant hover:text-primary transition-colors duration-200">
            <span className="material-symbols-outlined">language</span>
          </button>
          <div className="h-8 w-8 rounded-full bg-surface-container-high border border-border flex items-center justify-center ml-2">
            <span className="material-symbols-outlined text-on-surface-variant">person</span>
          </div>
        </div>
      </div>
    </header>
  )
}
