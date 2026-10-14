import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, RequireAuth } from './auth'
import { Layout } from './components'
import Admin from './pages/Admin'
import AuthPage from './pages/AuthPage'
import Dashboard from './pages/Dashboard'
import LessonView from './pages/LessonView'
import NewLesson from './pages/NewLesson'

const page = (element: React.ReactNode, role?: 'admin') => (
  <RequireAuth role={role}>
    <Layout>{element}</Layout>
  </RequireAuth>
)

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/register" element={<AuthPage mode="register" />} />
          <Route path="/" element={page(<Dashboard />)} />
          <Route path="/new" element={page(<NewLesson />)} />
          <Route path="/lessons/:id" element={page(<LessonView />)} />
          <Route path="/admin" element={page(<Admin />, 'admin')} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
