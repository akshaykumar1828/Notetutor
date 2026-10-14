import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api'
import { AuthProvider } from '../auth'
import { ProgressBar, StatusBadge, formatDuration } from '../components'
import AuthPage from '../pages/AuthPage'
import NewLesson from '../pages/NewLesson'

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )
}

describe('components', () => {
  it('labels each lesson status', () => {
    render(<><StatusBadge status="running" /><StatusBadge status="done" /></>)
    expect(screen.getByText('Generating')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })

  it('clamps progress', () => {
    render(<ProgressBar value={140} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('formats durations', () => {
    expect(formatDuration(125.4)).toBe('2:05')
    expect(formatDuration(null)).toBe('-')
  })
})

describe('api client', () => {
  it('turns FastAPI error details into readable errors', async () => {
    mockFetch(409, { detail: 'An account with this e-mail already exists' })
    await expect(api.register('a@b.co', 'A', 'password123')).rejects.toMatchObject({
      status: 409,
      message: 'An account with this e-mail already exists',
    })
  })

  it('joins validation error lists', async () => {
    mockFetch(422, { detail: [{ msg: 'field required' }, { msg: 'too short' }] })
    const error = await api.lessons().catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('field required, too short')
  })

  it('sends the session cookie', async () => {
    const spy = mockFetch(200, [])
    await api.lessons()
    expect(spy).toHaveBeenCalledWith('/api/lessons', expect.objectContaining({ credentials: 'include' }))
  })
})

describe('pages', () => {
  it('rejects short passwords before calling the server', async () => {
    const spy = mockFetch(401, { detail: 'Not authenticated' })
    render(<AuthProvider><MemoryRouter><AuthPage mode="register" /></MemoryRouter></AuthProvider>)
    await waitFor(() => expect(screen.getByLabelText('Password')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('Name'), 'Ada')
    await userEvent.type(screen.getByLabelText('E-mail'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'short')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByText('Password must be at least 8 characters.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledTimes(1) // only the initial /auth/me check
  })

  it('enables Generate only once there is something to generate from', async () => {
    render(<MemoryRouter><NewLesson /></MemoryRouter>)
    const generate = screen.getByRole('button', { name: 'Generate video' })
    expect(generate).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Type a topic' }))
    await userEvent.type(screen.getByLabelText('Topic'), 'How DNS works')
    expect(generate).toBeEnabled()
  })

  it('rejects unsupported file types', async () => {
    render(<MemoryRouter><NewLesson /></MemoryRouter>)
    const input = screen.getByTestId('file-input') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'tool.exe', { type: 'application/octet-stream' }), {
      applyAccept: false,
    })
    expect(screen.getByText('Unsupported file type .exe')).toBeInTheDocument()
  })
})
