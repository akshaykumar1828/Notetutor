import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Lesson, type Role, type User } from '../api'
import { useAuth } from '../auth'
import { ErrorNote, StatusBadge } from '../components'

export default function Admin() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.adminUsers(), api.adminLessons()])
      .then(([u, l]) => {
        setUsers(u)
        setLessons(l)
      })
      .catch((err) => setError(err.message))
  }, [])

  async function changeRole(target: User, role: Role) {
    try {
      const updated = await api.setRole(target.id, role)
      setUsers((all) => all.map((u) => (u.id === updated.id ? updated : u)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change role')
    }
  }

  const ownerName = (id: string) => users.find((u) => u.id === id)?.name ?? `#${id}`
  const count = (status: Lesson['status']) => lessons.filter((l) => l.status === status).length

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Admin</h1>
      <ErrorNote message={error} />

      <div className="grid gap-4 sm:grid-cols-4">
        {[['Users', users.length], ['Lessons', lessons.length], ['Ready', count('done')], ['Failed', count('failed')]].map(
          ([label, value]) => (
            <div key={label} className="card p-4">
              <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
              <p className="text-2xl font-bold">{value}</p>
            </div>
          ),
        )}
      </div>

      <section className="card overflow-x-auto p-0">
        <h2 className="px-5 pt-5 font-semibold">Users</h2>
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-slate-400">
            <tr><th className="px-5 py-2">Name</th><th className="px-5 py-2">E-mail</th><th className="px-5 py-2">Role</th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-ink-700">
                <td className="px-5 py-2.5">{u.name}</td>
                <td className="px-5 py-2.5 text-slate-300">{u.email}</td>
                <td className="px-5 py-2.5">
                  <select className="input w-28 py-1" value={u.role} disabled={u.id === me?.id}
                    onChange={(e) => changeRole(u, e.target.value as Role)}>
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card overflow-x-auto p-0">
        <h2 className="px-5 pt-5 font-semibold">All lessons</h2>
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-slate-400">
            <tr><th className="px-5 py-2">Title</th><th className="px-5 py-2">Owner</th><th className="px-5 py-2">Status</th><th className="px-5 py-2">Created</th></tr>
          </thead>
          <tbody>
            {lessons.map((l) => (
              <tr key={l.id} className="border-t border-ink-700">
                <td className="px-5 py-2.5"><Link className="hover:text-accent" to={`/lessons/${l.id}`}>{l.title}</Link></td>
                <td className="px-5 py-2.5 text-slate-300">{ownerName(l.owner_id)}</td>
                <td className="px-5 py-2.5"><StatusBadge status={l.status} /></td>
                <td className="px-5 py-2.5 text-slate-400">{new Date(l.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
