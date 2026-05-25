import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import DashboardPage from './pages/DashboardPage'
import AnalysisPage from './pages/AnalysisPage'

const router = createBrowserRouter([
  { path: '/', element: <DashboardPage /> },
  { path: '/analysis', element: <AnalysisPage /> },
])

export default function App() {
  return <RouterProvider router={router} />
}
