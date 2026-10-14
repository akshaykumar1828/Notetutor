import type { ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import type { LessonStatus } from './api'
import { useAuth } from './auth'

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${isActive ? 'bg-ink-700 text-white' : 'text-slate-300 hover:text-white'}`

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,_#1e2a46_0%,_#0b1020_60%)]">
      <header className="border-b border-ink-800/80 bg-ink-950/70 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-bold tracking-tight">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-ink-950">N</span>
            NoteTutor AI
          </Link>
          {user && (
            <nav className="flex flex-1 items-center gap-1">
              <NavLink to="/" end className={link}>My lessons</NavLink>
              <NavLink to="/new" className={link}>New lesson</NavLink>
              {user.role === 'admin' && <NavLink to="/admin" className={link}>Admin</NavLink>}
            </nav>
          )}
          {user && (
            <div className="flex items-center gap-3 text-sm">
              <span className="hidden text-slate-400 sm:inline">{user.name}</span>
              <button
                className="btn-ghost py-1.5"
                onClick={async () => {
                  await logout()
                  navigate('/login')
                }}
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  )
}

const STATUS_STYLE: Record<LessonStatus, string> = {
  queued: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  running: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  done: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  failed: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
}

const STATUS_LABEL: Record<LessonStatus, string> = {
  queued: 'Queued',
  running: 'Generating',
  done: 'Ready',
  failed: 'Failed',
}

export function StatusBadge({ status }: { status: LessonStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${STATUS_STYLE[status]}`}>
      {status === 'running' && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-300" />}
      {STATUS_LABEL[status]}
    </span>
  )
}

export function ProgressBar({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div className="h-2 overflow-hidden rounded-full bg-ink-700" role="progressbar" aria-valuenow={clamped}
      aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-gradient-to-r from-accent to-sky-300 transition-all duration-700"
        style={{ width: `${clamped}%` }} />
    </div>
  )
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null
  return <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-sm text-rose-200">{message}</p>
}

export function formatDuration(seconds: number | null): string {
  if (!seconds) return '-'
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
