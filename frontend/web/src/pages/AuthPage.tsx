import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorNote } from '../components'

const FEATURES = [
  { title: 'Any material', text: 'Handwritten pages, PDFs, text files or just a topic.' },
  { title: 'Synced animation', text: 'Diagrams, flows and tables appear as the narrator explains them.' },
  { title: 'Stays faithful', text: 'Every scene is fact-checked against your own notes.' },
]

const ICONS = {
  user: 'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',
  mail: 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm-1 1 9 7 9-7',
  lock: 'M6 11h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1zm2 0V7a4 4 0 0 1 8 0v4',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  eyeOff: 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2',
  check: 'M5 12l5 5 9-10',
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
      <path d={path} />
    </svg>
  )
}

function Field({ id, label, icon, children }: { id: string; label: string; icon: string; children: ReactNode }) {
  return (
    <div>
      <label className="label" htmlFor={id}>{label}</label>
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 left-3.5 flex items-center text-slate-500">
          <Icon path={icon} />
        </span>
        {children}
      </div>
    </div>
  )
}

/** Small looping preview of a generated scene: steps pop in, dots flow. */
function ScenePreview() {
  return (
    <div className="mt-10 rounded-2xl border border-ink-700 bg-ink-900/70 p-5 shadow-2xl shadow-black/40">
      <div className="mb-4 flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-400/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-300/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
        <span className="ml-3 text-xs text-slate-500">lesson.mp4 · Scene 2 of 6</span>
      </div>
      <div className="flex items-center gap-2">
        {['Notes', 'Lesson plan', 'Video'].map((step, i) => (
          <div key={step} className={`flex items-center gap-2 ${i < 2 ? 'flex-1' : ''}`}>
            <div className="auth-pop rounded-xl border border-accent/40 bg-accent/10 px-3 py-2 text-xs font-semibold text-sky-200"
              style={{ animationDelay: `${i * 0.6}s` }}>
              {step}
            </div>
            {i < 2 && (
              <div className="relative h-px flex-1 bg-ink-600">
                <span className="auth-dot absolute -top-[3px] h-1.5 w-1.5 rounded-full bg-focus"
                  style={{ animationDelay: `${i * 0.6 + 0.3}s` }} />
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-ink-700">
        <div className="auth-progress h-full rounded-full bg-gradient-to-r from-accent to-violet-400" />
      </div>
      <p className="mt-3 text-xs text-slate-400">"...and each step lights up exactly when it is explained."</p>
    </div>
  )
}

export default function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { user, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
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
    <div className="relative min-h-screen overflow-hidden bg-ink-950">
      {/* soft background glows */}
      <div className="pointer-events-none absolute -left-40 -top-40 h-[30rem] w-[30rem] rounded-full bg-accent/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-48 right-0 h-[28rem] w-[28rem] rounded-full bg-violet-500/15 blur-3xl" />

      <div className="relative mx-auto grid min-h-screen max-w-6xl grid-cols-1 items-center gap-12 px-4 py-10 lg:grid-cols-2 lg:px-8">
        {/* Showcase (wide screens only) */}
        <section className="hidden lg:block">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-br from-accent to-violet-400 text-lg font-bold text-ink-950 shadow-lg shadow-accent/30">N</div>
            <span className="text-lg font-bold tracking-tight">NoteTutor AI</span>
          </div>
          <h2 className="mt-10 text-4xl font-bold leading-tight tracking-tight">
            Turn your notes into
            <span className="block bg-gradient-to-r from-accent to-violet-300 bg-clip-text text-transparent">
              animated explainer videos.
            </span>
          </h2>
          <ul className="mt-8 space-y-4">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3">
                <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/15 text-accent">
                  <Icon path={ICONS.check} />
                </span>
                <div>
                  <p className="font-semibold text-slate-100">{f.title}</p>
                  <p className="text-sm text-slate-400">{f.text}</p>
                </div>
              </li>
            ))}
          </ul>
          <ScenePreview />
        </section>

        {/* Form */}
        <section className="mx-auto w-full min-w-0 max-w-md">
          <div className="mb-8 text-center lg:text-left">
            <div className="mx-auto mb-5 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-violet-400 text-xl font-bold text-ink-950 shadow-lg shadow-accent/30 lg:hidden">N</div>
            <h1 className="text-3xl font-bold tracking-tight">{isLogin ? 'Welcome back' : 'Create your account'}</h1>
            <p className="mt-2 text-sm text-slate-400">
              {isLogin ? 'Log in to see your lessons and make new ones.' : 'Make your first lesson in a few minutes - it is free.'}
            </p>
          </div>

          <form onSubmit={submit} className="card space-y-5 border-ink-600/60 bg-ink-900/70 p-7 backdrop-blur" noValidate>
            {!isLogin && (
              <Field id="name" label="Name" icon={ICONS.user}>
                <input id="name" className="input pl-10!" value={name} placeholder="Your name"
                  autoComplete="name" onChange={(e) => setName(e.target.value)} required />
              </Field>
            )}
            <Field id="email" label="E-mail" icon={ICONS.mail}>
              <input id="email" type="email" className="input pl-10!" value={email} autoComplete="email"
                placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Field id="password" label="Password" icon={ICONS.lock}>
              <input id="password" type={showPassword ? 'text' : 'password'} className="input pl-10! pr-11!"
                value={password} placeholder={isLogin ? 'Your password' : 'At least 8 characters'}
                autoComplete={isLogin ? 'current-password' : 'new-password'}
                onChange={(e) => setPassword(e.target.value)} required />
              <button type="button" onClick={() => setShowPassword((s) => !s)}
                className="absolute inset-y-0 right-2 my-auto grid h-8 w-8 place-items-center rounded-lg text-slate-500 transition hover:bg-ink-700 hover:text-slate-200"
                aria-label={showPassword ? 'Hide password' : 'Show password'}>
                <Icon path={showPassword ? ICONS.eyeOff : ICONS.eye} />
              </button>
            </Field>
            <ErrorNote message={error} />
            <button type="submit" disabled={busy}
              className="btn w-full bg-gradient-to-r from-accent to-violet-400 py-3 text-ink-950 shadow-lg shadow-accent/20 hover:brightness-110">
              {busy ? 'Please wait...' : isLogin ? 'Log in' : 'Create account'}
            </button>
            <p className="text-center text-sm text-slate-400">
              {isLogin ? "Don't have an account? " : 'Already registered? '}
              <Link className="font-semibold text-accent hover:underline" to={isLogin ? '/register' : '/login'}>
                {isLogin ? 'Sign up' : 'Log in'}
              </Link>
            </p>
          </form>
          <p className="mt-6 text-center text-xs text-slate-500">Free to use · Runs on free AI tiers · Your files stay in your account</p>
        </section>
      </div>
    </div>
  )
}
