// Typed client for the NoteTutor REST API. Authentication rides on the
// httpOnly session cookie set by the backend (credentials: 'include').

export type Role = 'user' | 'admin'
export type LessonStatus = 'queued' | 'running' | 'done' | 'failed'

export interface User {
  id: string
  email: string
  name: string
  role: Role
  created_at: string
}

export interface Lesson {
  id: string
  title: string
  source_kind: 'file' | 'topic'
  status: LessonStatus
  stage: string
  progress: number
  error: string | null
  duration_seconds: number | null
  scene_count: number | null
  options: { voice?: string; captions?: boolean; music?: boolean; verify?: boolean }
  created_at: string
  finished_at: string | null
  owner_id: string
}

export interface LessonDetail extends Lesson {
  has_video: boolean
  has_review: boolean
}

export interface SceneScript {
  title: string
  visual: string
  narration: string
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'include', ...init })
  if (!response.ok) {
    let message = response.statusText || 'Request failed'
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join(', ')
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  const type = response.headers.get('content-type') ?? ''
  return (type.includes('application/json') ? response.json() : response.text()) as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  register: (email: string, name: string, password: string) =>
    request<{ user: User }>('/auth/register', json('POST', { email, name, password })),
  login: (email: string, password: string) =>
    request<{ user: User }>('/auth/login', json('POST', { email, password })),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),

  lessons: () => request<Lesson[]>('/lessons'),
  lesson: (id: string) => request<LessonDetail>(`/lessons/${id}`),
  createLesson: (form: FormData) => request<Lesson>('/lessons', { method: 'POST', body: form }),
  deleteLesson: (id: string) => request<void>(`/lessons/${id}`, { method: 'DELETE' }),
  script: (id: string) => request<SceneScript[]>(`/lessons/${id}/script`),
  review: (id: string) => request<string>(`/lessons/${id}/review`),
  videoUrl: (id: string, download = false) => `/api/lessons/${id}/video${download ? '?download=true' : ''}`,

  adminUsers: () => request<User[]>('/admin/users'),
  adminLessons: () => request<Lesson[]>('/admin/lessons'),
  setRole: (id: string, role: Role) => request<User>(`/admin/users/${id}/role`, json('PATCH', { role })),
}
