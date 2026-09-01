import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import {
  Activity,
  Archive,
  Database,
  HardDrive,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import { api, Job, Repository } from './api'
import './styles.css'

const statusClass = (status: string) => `pill ${status}`

function Layout() {
  const nav = [
    ['/', Activity, 'Overview'],
    ['/repositories', Database, 'Repositories'],
    ['/backups', Play, 'Backups'],
    ['/archives', Archive, 'Archives'],
    ['/restore', RotateCcw, 'Restore'],
  ]

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <div className="brandmark">B</div>
          <div>
            <b>Borg Manager</b>
            <span>Backup control</span>
          </div>
        </div>
        <nav>
          {nav.map(([to, Icon, label]: any) => (
            <NavLink key={to} to={to} end={to === '/'}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebottom">
          <ShieldCheck size={18} /> Local-first & self-hosted
        </div>
      </aside>

      <main>
        <header>
          <div>
            <span className="eyebrow">BACKUP OPERATIONS</span>
            <h1>Keep every restore within reach.</h1>
          </div>
          <div className="badge">BorgBackup</div>
        </header>

        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/repositories" element={<Repositories />} />
          <Route path="/backups" element={<Backups />} />
          <Route path="/archives" element={<Archives />} />
          <Route path="/restore" element={<Restore />} />
        </Routes>
      </main>
    </div>
  )
}

function Dashboard() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    void api.get('/dashboard').then(setData)
  }, [])

  if (!data) return <Card>Loading…</Card>

  return (
    <>
      <div className="grid3">
        <Metric label="Repositories" value={data.repository_count} icon={<Database />} />
        <Metric label="Recent successes" value={data.successful_recent} icon={<ShieldCheck />} />
        <Metric label="Recent failures" value={data.failed_recent} icon={<Activity />} />
      </div>
      <section className="section">
        <div className="sectionTitle">
          <div>
            <span className="eyebrow">ACTIVITY</span>
            <h2>Recent backup jobs</h2>
          </div>
        </div>
        <Card>
          {data.recent_jobs.length ? (
            <table>
              <thead>
                <tr>
                  <th>Archive</th>
                  <th>Status</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_jobs.map((job: Job) => (
                  <tr key={job.id}>
                    <td>{job.archive_name}</td>
                    <td><span className={statusClass(job.status)}>{job.status}</span></td>
                    <td>{job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty text="No backup jobs yet." />
          )}
        </Card>
      </section>
    </>
  )
}

function Metric({ label, value, icon }: { label: string; value: any; icon: React.ReactNode }) {
  return (
    <div className="metric">
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="card">{children}</div>
}

function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <HardDrive size={28} />
      <b>{text}</b>
      <span>Your backup workspace is ready.</span>
    </div>
  )
}

function Repositories() {
  const [repos, setRepos] = useState<Repository[]>([])
  const [form, setForm] = useState({ name: '', location: '', passphrase: '' })

  const load = async () => {
    setRepos(await api.get<Repository[]>('/repositories'))
  }

  useEffect(() => {
    void load()
  }, [])

  async function add(event: React.FormEvent) {
    event.preventDefault()
    await api.post('/repositories', form)
    setForm({ name: '', location: '', passphrase: '' })
    await load()
  }

  return (
    <section className="section">
      <div className="sectionTitle">
        <div>
          <span className="eyebrow">STORAGE</span>
          <h2>Repositories</h2>
        </div>
      </div>
      <div className="split">
        <Card>
          <form onSubmit={add}>
            <label>
              Name
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="NAS archive"
                required
              />
            </label>
            <label>
              Repository location
              <input
                value={form.location}
                onChange={(event) => setForm({ ...form, location: event.target.value })}
                placeholder="/repos/main or ssh://user@host/./repo"
                required
              />
            </label>
            <label>
              Passphrase
              <input
                type="password"
                value={form.passphrase}
                onChange={(event) => setForm({ ...form, passphrase: event.target.value })}
                placeholder="Optional"
              />
            </label>
            <button><Plus size={16} />Add repository</button>
          </form>
        </Card>

        <Card>
          {repos.length ? (
            <div className="repoList">
              {repos.map((repo) => (
                <div className="repo" key={repo.id}>
                  <div className="repoIcon"><Database /></div>
                  <div>
                    <b>{repo.name}</b>
                    <code>{repo.location}</code>
                  </div>
                  <button
                    className="ghost"
                    onClick={async () => {
                      await api.del(`/repositories/${repo.id}`)
                      await load()
                    }}
                  >
                    Delete
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty text="No repositories configured." />
          )}
        </Card>
      </div>
    </section>
  )
}

function Backups() {
  const [repos, setRepos] = useState<Repository[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [repo, setRepo] = useState('')
  const [sources, setSources] = useState('/sources')
  const [archive, setArchive] = useState('')

  const load = () => {
    void api.get<Repository[]>('/repositories').then((result) => {
      setRepos(result)
      if (!repo && result[0]) setRepo(String(result[0].id))
    })
    void api.get<Job[]>('/jobs').then(setJobs)
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1500)
    return () => clearInterval(timer)
  }, [])

  async function run(event: React.FormEvent) {
    event.preventDefault()
    await api.post('/backups', {
      repository_id: Number(repo),
      sources: sources.split(',').map((item) => item.trim()).filter(Boolean),
      archive_name: archive || null,
      compression: 'zstd,3',
      excludes: [],
    })
    setArchive('')
    load()
  }

  return (
    <section className="section">
      <div className="sectionTitle">
        <div>
          <span className="eyebrow">EXECUTION</span>
          <h2>Backups</h2>
        </div>
      </div>
      <div className="split">
        <Card>
          <form onSubmit={run}>
            <label>
              Repository
              <select value={repo} onChange={(event) => setRepo(event.target.value)}>
                {repos.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label>
              Source paths
              <input value={sources} onChange={(event) => setSources(event.target.value)} placeholder="/sources/home, /sources/photos" />
            </label>
            <label>
              Archive name
              <input value={archive} onChange={(event) => setArchive(event.target.value)} placeholder="Generated automatically" />
            </label>
            <button disabled={!repo}><Play size={16} />Run backup</button>
          </form>
        </Card>

        <Card>
          {jobs.length ? (
            <div className="jobList">
              {jobs.slice(0, 12).map((job) => (
                <div className="job" key={job.id}>
                  <div>
                    <b>{job.archive_name}</b>
                    <small>Repository #{job.repository_id}</small>
                  </div>
                  <span className={statusClass(job.status)}>{job.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty text="No jobs yet." />
          )}
        </Card>
      </div>
    </section>
  )
}

function Archives() {
  const [repos, setRepos] = useState<Repository[]>([])
  const [repo, setRepo] = useState('')
  const [archives, setArchives] = useState<any[]>([])
  const [selected, setSelected] = useState('')
  const [files, setFiles] = useState<any[]>([])

  useEffect(() => {
    void api.get<Repository[]>('/repositories').then((result) => {
      setRepos(result)
      if (result[0]) setRepo(String(result[0].id))
    })
  }, [])

  async function load() {
    if (!repo) return
    setArchives(await api.get(`/repositories/${repo}/archives`))
    setFiles([])
  }

  async function browse(name: string) {
    setSelected(name)
    setFiles(await api.get(`/repositories/${repo}/archives/${encodeURIComponent(name)}/files`))
  }

  return (
    <section className="section">
      <div className="sectionTitle">
        <div>
          <span className="eyebrow">HISTORY</span>
          <h2>Archives</h2>
        </div>
        <div className="inline">
          <select value={repo} onChange={(event) => setRepo(event.target.value)}>
            {repos.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <button onClick={() => void load()}>Load archives</button>
        </div>
      </div>

      <div className="split">
        <Card>
          {archives.length ? archives.map((item) => (
            <button
              className={`archiveRow ${selected === item.name ? 'selected' : ''}`}
              onClick={() => void browse(item.name)}
              key={item.name}
            >
              <Archive size={17} />
              <span>
                <b>{item.name}</b>
                <small>{item.time || ''}</small>
              </span>
            </button>
          )) : <Empty text="Load a repository to browse archives." />}
        </Card>

        <Card>
          {files.length ? (
            <table>
              <thead><tr><th>Path</th><th>Size</th></tr></thead>
              <tbody>
                {files.map((file: any, index) => (
                  <tr key={index}>
                    <td className="path">{file.path}</td>
                    <td>{typeof file.size === 'number' ? formatBytes(file.size) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="Select an archive to browse files." />}
        </Card>
      </div>
    </section>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let index = -1
  do {
    bytes /= 1024
    index++
  } while (bytes >= 1024 && index < units.length - 1)
  return `${bytes.toFixed(1)} ${units[index]}`
}

function Restore() {
  const [repos, setRepos] = useState<Repository[]>([])
  const [repo, setRepo] = useState('')
  const [archive, setArchive] = useState('')
  const [paths, setPaths] = useState('')
  const [target, setTarget] = useState('restored')
  const [message, setMessage] = useState('')

  useEffect(() => {
    void api.get<Repository[]>('/repositories').then((result) => {
      setRepos(result)
      if (result[0]) setRepo(String(result[0].id))
    })
  }, [])

  async function restore(event: React.FormEvent) {
    event.preventDefault()
    setMessage('Restoring…')
    try {
      const result: any = await api.post('/restore', {
        repository_id: Number(repo),
        archive,
        paths: paths ? paths.split(',').map((item) => item.trim()) : [],
        target,
      })
      setMessage(`Restore complete: ${result.target}`)
    } catch (error: any) {
      setMessage(error.message)
    }
  }

  return (
    <section className="section">
      <div className="sectionTitle">
        <div>
          <span className="eyebrow">RECOVERY</span>
          <h2>Restore</h2>
        </div>
      </div>
      <Card>
        <form className="wideForm" onSubmit={restore}>
          <label>
            Repository
            <select value={repo} onChange={(event) => setRepo(event.target.value)}>
              {repos.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            Archive
            <input value={archive} onChange={(event) => setArchive(event.target.value)} required placeholder="backup-2026-09-01_10-00-00" />
          </label>
          <label>
            Paths
            <input value={paths} onChange={(event) => setPaths(event.target.value)} placeholder="Optional; comma-separated. Empty = full archive" />
          </label>
          <label>
            Target inside /restore
            <input value={target} onChange={(event) => setTarget(event.target.value)} required />
          </label>
          <button><RotateCcw size={16} />Restore archive</button>
          {message && <div className="notice">{message}</div>}
        </form>
      </Card>
    </section>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Layout />
    </BrowserRouter>
  </React.StrictMode>,
)
