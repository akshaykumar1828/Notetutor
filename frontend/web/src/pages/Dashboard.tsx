import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Lesson } from '../api'
import { ErrorNote, ProgressBar, StatusBadge, formatDuration } from '../components'

export default function Dashboard() {
  const [lessons, setLessons] = useState<Lesson[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let timer: number | undefined
    const load = () =>
      api.lessons()
        .then((data) => {
          setLessons(data)
          // Keep polling while anything is still being generated.
          if (data.some((l) => l.status === 'queued' || l.status === 'running')) {
            timer = window.setTimeout(load, 3000)
          }
        })
        .catch((err) => setError(err.message))
    load()
    return () => window.clearTimeout(timer)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">My lessons</h1>
          <p className="text-sm text-slate-400">Every video you have generated, newest first.</p>
        </div>
        <Link to="/new" className="btn-primary">+ New lesson</Link>
      </div>
      <ErrorNote message={error} />

      {lessons === null && !error && <p className="text-slate-400">Loading...</p>}
      {lessons?.length === 0 && (
        <div className="card text-center">
          <p className="text-lg font-semibold">No lessons yet</p>
          <p className="mt-1 text-sm text-slate-400">Upload your notes or type a topic to make your first video.</p>
          <Link to="/new" className="btn-primary mt-4">Create a lesson</Link>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {lessons?.map((lesson) => (
          <Link key={lesson.id} to={`/lessons/${lesson.id}`}
            className="card group block transition hover:-translate-y-0.5 hover:border-accent/60">
            <div className="mb-3 flex items-start justify-between gap-3">
              <h2 className="line-clamp-2 font-semibold group-hover:text-accent">{lesson.title}</h2>
              <StatusBadge status={lesson.status} />
            </div>
            {(lesson.status === 'running' || lesson.status === 'queued') && (
              <div className="space-y-2">
                <ProgressBar value={lesson.progress} />
                <p className="line-clamp-1 text-xs text-slate-400">{lesson.stage}</p>
              </div>
            )}
            {lesson.status === 'done' && (
              <p className="text-sm text-slate-400">
                {lesson.scene_count} scenes - {formatDuration(lesson.duration_seconds)}
              </p>
            )}
            {lesson.status === 'failed' && <p className="line-clamp-2 text-sm text-rose-300">{lesson.error}</p>}
            <p className="mt-3 text-xs text-slate-500">
              {lesson.source_kind === 'file' ? 'From your notes' : 'From a topic'} -{' '}
              {new Date(lesson.created_at).toLocaleString()}
            </p>
          </Link>
        ))}
      </div>
    </div>
  )
}
