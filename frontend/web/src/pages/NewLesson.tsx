import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { ErrorNote } from '../components'

export const VOICES = [
  { id: 'af_heart', label: 'Heart - warm, female' },
  { id: 'af_bella', label: 'Bella - bright, female' },
  { id: 'am_michael', label: 'Michael - male' },
  { id: 'bf_emma', label: 'Emma - British, female' },
  { id: 'bm_george', label: 'George - British, male' },
]
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.txt,.md'
// Matches the server limits, sized to the free AI tiers.
const MAX_MB = 10
const MAX_PAGES = 10

export default function NewLesson() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<'file' | 'topic'>('file')
  const [file, setFile] = useState<File | null>(null)
  const [topic, setTopic] = useState('')
  const [voice, setVoice] = useState('af_heart')
  const [captions, setCaptions] = useState(true)
  const [music, setMusic] = useState(true)
  const [verify, setVerify] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function pick(candidate: File | undefined) {
    if (!candidate) return
    const suffix = candidate.name.slice(candidate.name.lastIndexOf('.')).toLowerCase()
    if (!ACCEPT.split(',').includes(suffix)) return setError(`Unsupported file type ${suffix}`)
    if (candidate.size > MAX_MB * 1024 * 1024) return setError(`File is larger than ${MAX_MB} MB`)
    setError(null)
    setFile(candidate)
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    pick(event.dataTransfer.files[0])
  }

  const ready = mode === 'file' ? !!file : topic.trim().length > 2

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready) return
    const form = new FormData()
    if (mode === 'file' && file) form.append('file', file)
    else form.append('topic', topic.trim())
    form.append('voice', voice)
    form.append('captions', String(captions))
    form.append('music', String(music))
    form.append('verify', String(verify))
    setBusy(true)
    setError(null)
    try {
      const lesson = await api.createLesson(form)
      navigate(`/lessons/${lesson.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the lesson')
      setBusy(false)
    }
  }

  const tab = (value: 'file' | 'topic', label: string) => (
    <button type="button" onClick={() => setMode(value)}
      className={`flex-1 rounded-lg px-3 py-2 text-sm font-semibold ${mode === value ? 'bg-ink-700 text-white' : 'text-slate-400 hover:text-white'}`}>
      {label}
    </button>
  )

  return (
    <form onSubmit={submit} className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">New lesson</h1>
        <p className="text-sm text-slate-400">Upload handwritten notes, a PDF or text - or just name a topic.</p>
      </div>

      <div className="card space-y-5">
        <div className="flex gap-1 rounded-xl bg-ink-800 p-1">
          {tab('file', 'Upload notes')}
          {tab('topic', 'Type a topic')}
        </div>

        {mode === 'file' ? (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className={`cursor-pointer rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${dragging ? 'border-accent bg-accent/10' : 'border-ink-600 hover:border-accent/60'}`}
          >
            <input ref={inputRef} type="file" accept={ACCEPT} className="hidden" data-testid="file-input"
              onChange={(e) => pick(e.target.files?.[0])} />
            {file ? (
              <>
                <p className="font-semibold">{file.name}</p>
                <p className="text-sm text-slate-400">{(file.size / 1024 / 1024).toFixed(1)} MB - click to change</p>
              </>
            ) : (
              <>
                <p className="font-semibold">Drop your notes here or click to browse</p>
                <p className="text-sm text-slate-400">
                  PDF up to {MAX_PAGES} pages, PNG, JPG, TXT or MD - up to {MAX_MB} MB
                </p>
              </>
            )}
          </div>
        ) : (
          <div>
            <label className="label" htmlFor="topic">Topic</label>
            <input id="topic" className="input" placeholder="e.g. How DNS resolution works" maxLength={300}
              value={topic} onChange={(e) => setTopic(e.target.value)} />
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="voice">Narrator voice</label>
            <select id="voice" className="input" value={voice} onChange={(e) => setVoice(e.target.value)}>
              {VOICES.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </div>
          <fieldset className="space-y-2 text-sm">
            <legend className="label">Options</legend>
            {([
              ['Captions', captions, setCaptions],
              ['Background music', music, setMusic],
              ['Fact-check every scene', verify, setVerify],
            ] as const).map(([label, value, set]) => (
              <label key={label} className="flex items-center gap-2 text-slate-300">
                <input type="checkbox" className="h-4 w-4 accent-[#78aaff]" checked={value}
                  onChange={(e) => set(e.target.checked)} />
                {label}
              </label>
            ))}
          </fieldset>
        </div>

        <ErrorNote message={error} />
        <button type="submit" className="btn-primary w-full" disabled={!ready || busy}>
          {busy ? 'Starting...' : 'Generate video'}
        </button>
        <p className="text-center text-xs text-slate-500">
          A one-page note takes about 2-3 minutes; you can leave this page while it runs.
        </p>
      </div>
    </form>
  )
}
