import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type LessonDetail, type SceneScript } from '../api'
import { ErrorNote, ProgressBar, StatusBadge, formatDuration } from '../components'

export default function LessonView() {
  const id = useParams().id ?? ''
  const navigate = useNavigate()
  const [lesson, setLesson] = useState<LessonDetail | null>(null)
  const [script, setScript] = useState<SceneScript[]>([])
  const [review, setReview] = useState<string | null>(null)
  const [tab, setTab] = useState<'script' | 'review'>('script')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let timer: number | undefined
    const load = () =>
      api.lesson(id)
        .then((data) => {
          setLesson(data)
          if (data.status === 'queued' || data.status === 'running') timer = window.setTimeout(load, 2500)
          if (data.status === 'done') {
            api.script(id).then(setScript).catch(() => setScript([]))
            if (data.has_review) api.review(id).then(setReview).catch(() => setReview(null))
          }
        })
        .catch((err) => setError(err.message))
    load()
    return () => window.clearTimeout(timer)
  }, [id])

  async function remove() {
    if (!window.confirm('Delete this lesson and its video?')) return
    try {
      await api.deleteLesson(id)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete')
    }
  }

  if (error && !lesson) return <ErrorNote message={error} />
  if (!lesson) return <p className="text-slate-400">Loading...</p>

  const working = lesson.status === 'queued' || lesson.status === 'running'
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/" className="text-sm text-slate-400 hover:text-white">&larr; My lessons</Link>
          <h1 className="mt-1 text-2xl font-bold">{lesson.title}</h1>
          <div className="mt-2 flex items-center gap-3 text-sm text-slate-400">
            <StatusBadge status={lesson.status} />
            {lesson.status === 'done' && <span>{lesson.scene_count} scenes - {formatDuration(lesson.duration_seconds)}</span>}
          </div>
        </div>
        {!working && <button className="btn-ghost" onClick={remove}>Delete</button>}
      </div>
      <ErrorNote message={error} />

      {working && (
        <div className="card space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">{lesson.stage}</span>
            <span className="text-slate-400">{lesson.progress}%</span>
          </div>
          <ProgressBar value={lesson.progress} />
          <p className="text-xs text-slate-500">This page updates by itself. You can leave and come back later.</p>
        </div>
      )}

      {lesson.status === 'failed' && (
        <div className="card space-y-2">
          <p className="font-semibold text-rose-300">Generation failed</p>
          <p className="whitespace-pre-wrap text-sm text-slate-300">{lesson.error}</p>
          <Link to="/new" className="btn-primary mt-2">Try again</Link>
        </div>
      )}

      {lesson.has_video && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="space-y-3">
            <video className="aspect-video w-full rounded-2xl border border-ink-700 bg-black"
              src={api.videoUrl(id)} controls preload="metadata" />
            <a className="btn-primary" href={api.videoUrl(id, true)}>Download MP4</a>
          </div>
          <div className="card max-h-[32rem] overflow-y-auto p-0">
            <div className="sticky top-0 flex gap-1 border-b border-ink-700 bg-ink-900 p-2">
              {(['script', 'review'] as const).map((name) => (
                <button key={name} onClick={() => setTab(name)}
                  className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === name ? 'bg-ink-700 text-white' : 'text-slate-400'}`}>
                  {name === 'script' ? 'Narration' : 'Fact review'}
                </button>
              ))}
            </div>
            <div className="space-y-4 p-5 text-sm leading-relaxed">
              {tab === 'script' && script.map((scene, i) => (
                <div key={i}>
                  <p className="font-semibold text-accent">{i + 1}. {scene.title}
                    <span className="ml-2 text-xs font-normal text-slate-500">{scene.visual.replace('_', ' ')}</span></p>
                  <p className="mt-1 text-slate-300">{scene.narration}</p>
                </div>
              ))}
              {tab === 'review' && (review
                ? <pre className="whitespace-pre-wrap font-sans text-slate-300">{review}</pre>
                : <p className="text-slate-400">No fact review for this lesson.</p>)}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
