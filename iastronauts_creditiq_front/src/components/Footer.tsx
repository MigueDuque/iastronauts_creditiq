export default function Footer() {
  return (
    <footer className="bg-background border-t border-border mt-auto">
      <div className="w-full px-margin-mobile md:px-margin-desktop py-6 flex flex-col md:flex-row justify-between items-center gap-4 max-w-[1440px] mx-auto">
        <div className="flex items-center gap-2 text-label-sm font-label-sm text-outline">
          <span className="material-symbols-outlined text-[15px] text-primary" style={{ fontVariationSettings: "'FILL' 1" }}>shield</span>
          © 2026 CreditIQ Intelligence Systems · Multi-tenant · NIIF-aware
        </div>
        <nav className="flex gap-5">
          {['Privacy', 'Terms', 'Security', 'Support'].map((label) => (
            <a key={label} href="#" className="text-label-sm font-label-sm text-on-surface-variant hover:text-secondary transition-colors duration-200">{label}</a>
          ))}
        </nav>
      </div>
    </footer>
  )
}
