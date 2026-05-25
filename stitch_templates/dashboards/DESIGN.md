---
name: CreditIQ
colors:
  surface: '#161B22'
  surface-dim: '#11131c'
  surface-bright: '#373943'
  surface-container-lowest: '#0c0e16'
  surface-container-low: '#191b24'
  surface-container: '#1d1f28'
  surface-container-high: '#282933'
  surface-container-highest: '#33343e'
  on-surface: '#e2e1ee'
  on-surface-variant: '#c3c5d8'
  inverse-surface: '#e2e1ee'
  inverse-on-surface: '#2e303a'
  outline: '#8d90a2'
  outline-variant: '#434656'
  surface-tint: '#b7c4ff'
  primary: '#b7c4ff'
  on-primary: '#002682'
  primary-container: '#2e62ff'
  on-primary-container: '#f7f6ff'
  inverse-primary: '#024cec'
  secondary: '#a6e6ff'
  on-secondary: '#003543'
  secondary-container: '#14d1ff'
  on-secondary-container: '#00566b'
  tertiary: '#ffb599'
  on-tertiary: '#5a1c00'
  tertiary-container: '#c64700'
  on-tertiary-container: '#fff5f2'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#dce1ff'
  primary-fixed-dim: '#b7c4ff'
  on-primary-fixed: '#001552'
  on-primary-fixed-variant: '#0039b5'
  secondary-fixed: '#b7eaff'
  secondary-fixed-dim: '#4cd6ff'
  on-secondary-fixed: '#001f28'
  on-secondary-fixed-variant: '#004e60'
  tertiary-fixed: '#ffdbce'
  tertiary-fixed-dim: '#ffb599'
  on-tertiary-fixed: '#370e00'
  on-tertiary-fixed-variant: '#7f2b00'
  background: '#0A0C10'
  on-background: '#e2e1ee'
  surface-variant: '#33343e'
  border: '#30363D'
  risk-high: '#F85149'
  risk-medium: '#D29922'
  risk-low: '#3FB950'
  data-indigo: '#6366F1'
typography:
  display-lg:
    fontFamily: Geist
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.05em
  headline-lg-mobile:
    fontFamily: Geist
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  margin-desktop: 32px
  margin-mobile: 16px
  gutter: 24px
  base-unit: 4px
  container-max-width: 1440px
---

# Product Vision  Design a modern enterprise-grade AI financial platform UI called **CreditIQ**.  CreditIQ is an AI-powered multi-agent financial analysis platform for enterprise financial teams.  The platform analyzes:  - Financial statements - NIIF notes - Executive reports - Financial risks - Portfolio performance - Corporate disclosures  The system uses AI agents to:  - Extract financial data - Analyze financial variations - Detect risks - Generate NIIF notes - Create executive summaries - Generate board-ready reports  The platform is designed for:  - Financial analysts - Accountants - Auditors - CFO teams - Executive leadership - Risk management teams  ---  # Design Objective  The UI must feel like:  - Bloomberg Terminal meets modern AI SaaS - Enterprise-grade - Premium financial platform - Intelligent - Executive-ready - Minimal but powerful - Highly visual - AI-native  The design should communicate:  - Trust - Intelligence - Financial professionalism - Advanced analytics - Enterprise security  ---  # Main Design Style  ## Design Language  - Modern enterprise SaaS - Dark mode preferred - Elegant financial dashboard aesthetics - Subtle glassmorphism - Clean spacing - Highly structured layouts - Premium typography - Soft shadows - Minimalistic but data-rich  ---  # Visual Inspiration  Use inspiration from:  - Stripe Dashboard - Notion AI - Bloomberg - Vercel - Linear - Palantir - Ramp - Figma enterprise dashboards  ---  # Main User Flow  1. User uploads financial documents 2. AI agents process documents 3. User sees extraction progress 4. User sees financial insights 5. User reviews risk scoring 6. User reviews generated NIIF notes 7. User exports executive reports  ---  # Required Screens  ---  # 1. Login Screen  ## Requirements  - Premium enterprise feel - Cognito-style secure login - Subtle AI branding - Dark theme - Financial professionalism  ## Include  - Email input - Password input - SSO button - Tenant/company selector - AI-powered branding visuals  ---  # 2. Main Dashboard  ## This is the core screen.  ## Requirements  - Executive dashboard - AI insights - Financial KPIs - Processing jobs - Recent analyses - Risk overview  ## Include  - AI-generated executive summary card - Financial health indicators - Risk score widgets - Uploaded document status - Processing pipeline visualization - Historical trends - Quick actions  ## Dashboard Feeling  The dashboard should feel:  - Data-driven - Intelligent - Executive-ready  ---  # 3. Document Upload Screen  ## Requirements  - Drag & drop upload - Modern upload experience - Enterprise file management  ## Include  - Upload area - Supported file formats - Upload progress - Document validation - Extraction progress - AI processing status  ## Supported Files  - PDFs - Excel - CSV - PPTX  ---  # 4. AI Analysis Screen  ## This is the WOW factor screen.  ## Requirements  - Visualize AI reasoning - Financial insights - Materiality analysis - NIIF findings - Anomaly detection  ## Include  - Financial tables - Account variation charts - AI-generated insights - Highlighted anomalies - Risk indicators - Confidence scores - Expandable reasoning panels  ## The Screen Must Show  - AI working - Financial intelligence - Enterprise analytics  ---  # 5. Risk Scoring Screen  ## Requirements  - Enterprise risk monitoring - Compliance validation - Anti-hallucination confidence  ## Include  - Overall risk score - Validation score - Compliance flags - Human review requirements - Issue severity indicators - Audit traceability  ## Style  - Premium risk dashboard - Executive compliance center  ---  # 6. Report Generator Screen  ## Requirements  - Executive-ready report management  ## Include  - Generated NIIF notes - Executive summaries - Board presentation previews - PDF export - Markdown export - PPT export  ## The UI Should Feel  - Premium - Polished - Boardroom-ready  ---  # 7. AI Agent Pipeline Visualization  ##