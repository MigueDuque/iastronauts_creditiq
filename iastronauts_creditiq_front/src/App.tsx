import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import AppLayout from './components/AppLayout'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const AnalysisPage  = lazy(() => import('./pages/AnalysisPage'))
const JobResultPage = lazy(() => import('./pages/JobResultPage'))
const MarketsPage   = lazy(() => import('./pages/MarketsPage'))

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true,         element: <Suspense fallback={null}><DashboardPage /></Suspense> },
      { path: 'analysis',    element: <Suspense fallback={null}><AnalysisPage /></Suspense> },
      { path: 'jobs/:jobId', element: <Suspense fallback={null}><JobResultPage /></Suspense> },
      { path: 'markets',     element: <Suspense fallback={null}><MarketsPage /></Suspense> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
