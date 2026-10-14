import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorNote } from '../components'

export default function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { user, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (mode === 'register' && password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setBusy(true)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, name, password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? '/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  const isLogin = mode === 'login'
  return (
    <div className="grid min-h-screen place-items-center bg-[radial-gradient(ellipse_at_top,_#1e2a46_0%,_#0b1020_60%)] px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent text-xl font-bold text-ink-950">N</div>
          <h1 className="text-2xl font-bold">{isLogin ? 'Welcome back' : 'Create your account'}</h1>
          <p className="mt-1 text-sm text-slate-400">Turn your notes into animated explainer videos.</p>
        </div>
        <form onSubmit={submit} className="card space-y-4" noValidate>
          {!isLogin && (
            <div>
              <label className="label" htmlFor="name">Name</label>
              <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
          )}
          <div>
            <label className="label" htmlFor="email">E-mail</label>
            <input id="email" type="email" className="input" value={email} autoComplete="email"
              onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" type="password" className="input" value={password}
              autoComplete={isLogin ? 'current-password' : 'new-password'}
              onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <ErrorNote message={error} />
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? 'Please wait...' : isLogin ? 'Log in' : 'Create account'}
          </button>
          <p className="text-center text-sm text-slate-400">
            {isLogin ? "Don't have an account? " : 'Already registered? '}
            <Link className="font-medium text-accent hover:underline" to={isLogin ? '/register' : '/login'}>
              {isLogin ? 'Sign up' : 'Log in'}
            </Link>
          </p>
        </form>
      </div>
    </div>
  )
}
