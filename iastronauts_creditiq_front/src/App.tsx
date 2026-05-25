import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import DashboardPage from './pages/DashboardPage'
import AnalysisPage from './pages/AnalysisPage'
import JobResultPage from './pages/JobResultPage'

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'analysis', element: <AnalysisPage /> },
      { path: 'jobs/:jobId', element: <JobResultPage /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
