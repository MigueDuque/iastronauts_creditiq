export default function Footer() {
  return (
    <footer className="bg-background border-t border-border mt-auto">
      <div className="w-full px-margin-mobile md:px-margin-desktop py-6 flex flex-col md:flex-row justify-between items-center gap-4 max-w-container-max-width mx-auto">
        <div className="text-label-md font-label-md font-bold text-outline">
          © 2025 CreditIQ Intelligence Systems. All rights reserved.
        </div>
        <nav className="flex gap-4">
          <a href="#" className="text-label-sm font-label-sm text-outline-variant hover:text-on-surface transition-colors duration-200">Privacy Policy</a>
          <a href="#" className="text-label-sm font-label-sm text-outline-variant hover:text-on-surface transition-colors duration-200">Terms of Service</a>
          <a href="#" className="text-label-sm font-label-sm text-outline-variant hover:text-on-surface transition-colors duration-200">Security Architecture</a>
          <a href="#" className="text-label-sm font-label-sm text-outline-variant hover:text-on-surface transition-colors duration-200">Contact Support</a>
        </nav>
      </div>
    </footer>
  )
}
