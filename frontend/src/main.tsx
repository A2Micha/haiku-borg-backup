import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import {
  Activity, Archive, CalendarClock, Database, HardDrive, Play, Plus,
  RotateCcw, ServerCog, ShieldCheck, TerminalSquare, Trash2, Wrench,
  CheckCircle2, AlertTriangle, Clock3
} from 'lucide-react'
import { api, Health, Job, Repository, Schedule } from './api'
import './styles.css'

const statusClass=(s:string)=>`pill ${s}`
const fmt=(v:string|null)=>v?new Date(v).toLocaleString():'—'

function Layout(){
  const [health,setHealth]=useState<Health|null>(null)
  useEffect(()=>{ void api.get<Health>('/health').then(setHealth).catch(()=>setHealth(null)) },[])
  const nav=[
    ['/',Activity,'Dashboard'],['/repositories',Database,'Repositories'],['/backups',Play,'Backups'],
    ['/archives',Archive,'Archives'],['/schedules',CalendarClock,'Schedules'],['/restore',RotateCcw,'Restore'],['/jobs',TerminalSquare,'Jobs']
  ]
  return <div className="shell">
    <aside>
      <div className="brand"><div className="brandmark">B</div><div><b>Borg Manager</b><span>Backup control</span></div></div>
      <nav>{nav.map(([to,Icon,label]:any)=><NavLink key={to} to={to} end={to==='/' }><Icon size={18}/>{label}</NavLink>)}</nav>
      <div className="sidebottom">
        <div className={`healthDot ${health?.mock_borg?'warn':health?.borg_available?'ok':'bad'}`}/>
        <div><b>{health?.mock_borg?'Mock mode':health?.borg_available?'Borg online':'Borg unavailable'}</b><span>{health?.timezone||'checking…'}</span></div>
      </div>
    </aside>
    <main>
      <header><div><span className="eyebrow">BACKUP OPERATIONS</span><h1>Keep every restore within reach.</h1></div><div className="topBadges"><span className="badge">BorgBackup</span>{health?.scheduler_running&&<span className="badge green">Scheduler active</span>}</div></header>
      {health?.mock_borg&&<div className="warningBanner"><AlertTriangle size={18}/><b>MOCK_BORG=1</b><span>Backups and repository operations are simulated. Set MOCK_BORG=0 in .env and rebuild/restart.</span></div>}
      <Routes>
        <Route path="/" element={<Dashboard/>}/><Route path="/repositories" element={<Repositories/>}/><Route path="/backups" element={<Backups/>}/>
        <Route path="/archives" element={<Archives/>}/><Route path="/schedules" element={<Schedules/>}/><Route path="/restore" element={<Restore/>}/><Route path="/jobs" element={<Jobs/>}/>
      </Routes>
    </main>
  </div>
}

function Dashboard(){
  const [data,setData]=useState<any>(null)
  const [schedules,setSchedules]=useState<Schedule[]>([])
  const load=async()=>{ setData(await api.get('/dashboard')); setSchedules(await api.get('/schedules')) }
  useEffect(()=>{ void load(); const t=setInterval(()=>void load(),5000); return()=>clearInterval(t) },[])
  if(!data)return <Card>Loading…</Card>
  return <>
    <div className="grid4">
      <Metric label="Repositories" value={data.repository_count} icon={<Database/>}/>
      <Metric label="Active schedules" value={data.active_schedule_count} icon={<CalendarClock/>}/>
      <Metric label="Running / queued" value={data.running_count} icon={<Clock3/>}/>
      <Metric label="Recent successes" value={data.successful_recent} icon={<ShieldCheck/>}/>
    </div>
    <div className="dashboardGrid">
      <section className="section"><SectionTitle eyebrow="ACTIVITY" title="Recent backup jobs"/><Card>{data.recent_jobs.length?<JobTable jobs={data.recent_jobs}/>:<Empty text="No backup jobs yet."/>}</Card></section>
      <section className="section"><SectionTitle eyebrow="AUTOMATION" title="Next scheduled backups"/><Card>{schedules.length?<div className="stack">{schedules.slice(0,6).map(s=><div className="listRow" key={s.id}><CalendarClock size={18}/><div><b>{s.name}</b><small>{s.cron} · repo #{s.repository_id}</small></div><span className="rightMeta">{s.next_run_at?fmt(s.next_run_at):s.enabled?'pending':'disabled'}</span></div>)}</div>:<Empty text="No schedules configured."/>}</Card></section>
    </div>
  </>
}

function Repositories(){
  const [repos,setRepos]=useState<Repository[]>([])
  const [form,setForm]=useState({name:'',location:'/repos/',passphrase:'',initialize:true,encryption:'repokey-blake2'})
  const [busy,setBusy]=useState(false); const [msg,setMsg]=useState('')
  const load=()=>api.get<Repository[]>('/repositories').then(setRepos)
  useEffect(()=>{ void load() },[])
  async function add(e:React.FormEvent){
    e.preventDefault(); setBusy(true); setMsg(form.initialize?'Creating real Borg repository…':'Verifying existing Borg repository…')
    try { await api.post('/repositories',form); setMsg('Repository verified and saved.'); setForm({...form,name:'',passphrase:'',location:'/repos/'}); await load() }
    catch(e:any){setMsg(e.message)} finally {setBusy(false)}
  }
  async function status(r:Repository){ setMsg(`Running borg check on ${r.name}…`); try{ await api.post(`/repositories/${r.id}/check`); setMsg(`${r.name}: Borg check successful.`) }catch(e:any){setMsg(e.message)} }
  return <section className="section"><SectionTitle eyebrow="STORAGE" title="Repositories" subtitle="Create a new Borg repository or attach an existing one."/>
    <div className="split"><Card><form onSubmit={add}>
      <div className="segmented"><button type="button" className={form.initialize?'active':''} onClick={()=>setForm({...form,initialize:true})}>Create new</button><button type="button" className={!form.initialize?'active':''} onClick={()=>setForm({...form,initialize:false})}>Connect existing</button></div>
      <label>Name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="Main backup" required/></label>
      <label>Repository location<input value={form.location} onChange={e=>setForm({...form,location:e.target.value})} placeholder="/repos/main or ssh://user@host/./repo" required/></label>
      {form.initialize&&<label>Encryption<select value={form.encryption} onChange={e=>setForm({...form,encryption:e.target.value})}><option value="repokey-blake2">repokey-blake2</option><option value="none">none</option></select></label>}
      <label>Passphrase<input type="password" value={form.passphrase} onChange={e=>setForm({...form,passphrase:e.target.value})} placeholder={form.initialize&&form.encryption!=='none'?'Required for encrypted repository':'Optional for unencrypted/existing repo'}/></label>
      <button disabled={busy}><Plus size={16}/>{busy?'Working…':form.initialize?'Create with borg init':'Verify & connect'}</button>
      <Hint>Local repositories must use a container-visible path such as <code>/repos/name</code>. SSH repositories can use Borg SSH syntax.</Hint>{msg&&<Notice text={msg}/>} </form></Card>
      <Card>{repos.length?<div className="stack">{repos.map(r=><div className="repoCard" key={r.id}><div className="repoIcon"><Database/></div><div className="grow"><b>{r.name}</b><code>{r.location}</code><small>Added {fmt(r.created_at)}</small></div><div className="actions"><button className="ghost" onClick={()=>void status(r)}><CheckCircle2 size={15}/>Check</button><button className="danger ghost" onClick={async()=>{if(confirm(`Remove ${r.name} from Borg Manager? The Borg data itself is not deleted.`)){await api.del('/repositories/'+r.id);await load()}}}><Trash2 size={15}/></button></div></div>)}</div>:<Empty text="No repositories configured."/>}</Card>
    </div></section>
}

function Backups(){
  const [repos,setRepos]=useState<Repository[]>([]); const [jobs,setJobs]=useState<Job[]>([]); const [repo,setRepo]=useState('')
  const [sources,setSources]=useState('/host'); const [archive,setArchive]=useState(''); const [compression,setCompression]=useState('zstd,3'); const [excludes,setExcludes]=useState(''); const [msg,setMsg]=useState('')
  const load=async()=>{ const r=await api.get<Repository[]>('/repositories'); setRepos(r); setRepo(v=>v||String(r[0]?.id||'')); setJobs(await api.get<Job[]>('/jobs')) }
  useEffect(()=>{ void load(); const t=setInterval(()=>void load(),2000); return()=>clearInterval(t) },[])
  async function run(e:React.FormEvent){ e.preventDefault(); setMsg('Queueing real Borg backup…'); try{ await api.post('/backups',{repository_id:Number(repo),sources:sources.split(',').map(x=>x.trim()).filter(Boolean),archive_name:archive||null,compression,excludes:excludes.split(',').map(x=>x.trim()).filter(Boolean)}); setArchive('');setMsg('Backup queued. Watch Jobs for live output.');await load() }catch(e:any){setMsg(e.message)} }
  return <section className="section"><SectionTitle eyebrow="EXECUTION" title="Backups" subtitle="Sources are read from the host mount at /host."/><div className="split"><Card><form onSubmit={run}>
    <label>Repository<select value={repo} onChange={e=>setRepo(e.target.value)}>{repos.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
    <label>Source paths<input value={sources} onChange={e=>setSources(e.target.value)} placeholder="/host/home, /host/etc"/></label>
    <label>Exclude patterns<input value={excludes} onChange={e=>setExcludes(e.target.value)} placeholder="/host/home/*/.cache, *.tmp"/></label>
    <label>Compression<select value={compression} onChange={e=>setCompression(e.target.value)}><option>zstd,3</option><option>zstd,6</option><option>lz4</option><option>none</option></select></label>
    <label>Archive name<input value={archive} onChange={e=>setArchive(e.target.value)} placeholder="Generated automatically"/></label>
    <button disabled={!repo}><Play size={16}/>Run Borg backup</button><Hint>Docker must expose the host data. Set <code>HOST_ROOT=/</code> (broad read access) or a narrower host directory in <code>.env</code>.</Hint>{msg&&<Notice text={msg}/>}</form></Card>
    <Card>{jobs.length?<div className="stack">{jobs.slice(0,15).map(j=><JobRow key={j.id} job={j}/>)}</div>:<Empty text="No jobs yet."/>}</Card></div></section>
}

function Schedules(){
  const [repos,setRepos]=useState<Repository[]>([]); const [items,setItems]=useState<Schedule[]>([]); const [msg,setMsg]=useState('')
  const [form,setForm]=useState({name:'Nightly',repository_id:'',cron:'0 2 * * *',sources:'/host',compression:'zstd,3',excludes:''})
  const load=async()=>{ const r=await api.get<Repository[]>('/repositories'); setRepos(r); setForm(f=>({...f,repository_id:f.repository_id||String(r[0]?.id||'')})); setItems(await api.get('/schedules')) }
  useEffect(()=>{ void load(); const t=setInterval(()=>void load(),10000); return()=>clearInterval(t) },[])
  async function create(e:React.FormEvent){ e.preventDefault(); try{ await api.post('/schedules',{name:form.name,repository_id:Number(form.repository_id),cron:form.cron,sources:form.sources.split(',').map(x=>x.trim()).filter(Boolean),compression:form.compression,excludes:form.excludes.split(',').map(x=>x.trim()).filter(Boolean),enabled:true});setMsg('Schedule registered in the live scheduler.');await load() }catch(e:any){setMsg(e.message)} }
  return <section className="section"><SectionTitle eyebrow="AUTOMATION" title="Schedules" subtitle="Cron schedules are executed by the backend scheduler, not just stored in SQLite."/><div className="split"><Card><form onSubmit={create}>
    <label>Name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})} required/></label><label>Repository<select value={form.repository_id} onChange={e=>setForm({...form,repository_id:e.target.value})}>{repos.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
    <label>Cron<input value={form.cron} onChange={e=>setForm({...form,cron:e.target.value})} placeholder="0 2 * * *" required/></label><label>Sources<input value={form.sources} onChange={e=>setForm({...form,sources:e.target.value})} placeholder="/host/home, /host/etc" required/></label>
    <label>Compression<select value={form.compression} onChange={e=>setForm({...form,compression:e.target.value})}><option>zstd,3</option><option>zstd,6</option><option>lz4</option></select></label><label>Excludes<input value={form.excludes} onChange={e=>setForm({...form,excludes:e.target.value})} placeholder="*.tmp, /host/home/*/.cache"/></label>
    <button disabled={!form.repository_id}><CalendarClock size={16}/>Create schedule</button><Hint>Example: <code>0 2 * * *</code> = every day at 02:00 in the configured TZ.</Hint>{msg&&<Notice text={msg}/>}</form></Card>
    <Card>{items.length?<div className="stack">{items.map(s=><div className="scheduleCard" key={s.id}><div className="scheduleIcon"><CalendarClock/></div><div className="grow"><b>{s.name}</b><code>{s.cron}</code><small>Next: {s.next_run_at?fmt(s.next_run_at):'not scheduled'} · {s.sources.join(', ')}</small></div><div className="actions"><button className="ghost" onClick={async()=>{try{await api.post(`/schedules/${s.id}/run`);setMsg(`${s.name} queued now.`)}catch(e:any){setMsg(e.message)}}}><Play size={15}/>Run now</button><button className="danger ghost" onClick={async()=>{await api.del(`/schedules/${s.id}`);await load()}}><Trash2 size={15}/></button></div></div>)}</div>:<Empty text="No schedules configured."/>}</Card></div></section>
}

function Jobs(){
  const [jobs,setJobs]=useState<Job[]>([]); const [selected,setSelected]=useState<number|null>(null)
  const load=async()=>setJobs(await api.get('/jobs'))
  useEffect(()=>{ void load(); const t=setInterval(()=>void load(),1500); return()=>clearInterval(t) },[])
  const active=useMemo(()=>jobs.find(j=>j.id===selected)||jobs[0], [jobs,selected])
  return <section className="section"><SectionTitle eyebrow="OBSERVABILITY" title="Backup jobs" subtitle="Real Borg stdout/stderr and exit status."/><div className="jobsGrid"><Card><div className="stack">{jobs.map(j=><button className={`jobSelect ${active?.id===j.id?'selected':''}`} key={j.id} onClick={()=>setSelected(j.id)}><div><b>{j.archive_name}</b><small>{fmt(j.started_at)}</small></div><span className={statusClass(j.status)}>{j.status}</span></button>)}</div></Card><Card>{active?<><div className="jobHeader"><div><span className={statusClass(active.status)}>{active.status}</span><h2>{active.archive_name}</h2><small>Repository #{active.repository_id} · Return code {active.return_code??'—'} · {fmt(active.started_at)}</small></div>{active.status==='running'&&<button className="danger" onClick={()=>void api.post(`/jobs/${active.id}/cancel`)}>Cancel</button>}</div><pre className="terminal">{active.log||'Waiting for Borg output…'}</pre></>:<Empty text="No jobs yet."/>}</Card></div></section>
}

function Archives(){
  const [repos,setRepos]=useState<Repository[]>([]); const [repo,setRepo]=useState(''); const [archives,setArchives]=useState<any[]>([]); const [selected,setSelected]=useState(''); const [files,setFiles]=useState<any[]>([]); const [msg,setMsg]=useState('')
  useEffect(()=>{ void api.get<Repository[]>('/repositories').then(r=>{setRepos(r);if(r[0])setRepo(String(r[0].id))}) },[])
  async function load(){if(!repo)return;try{setArchives(await api.get(`/repositories/${repo}/archives`));setFiles([]);setMsg('')}catch(e:any){setMsg(e.message)}}
  async function browse(name:string){try{setSelected(name);setFiles(await api.get(`/repositories/${repo}/archives/${encodeURIComponent(name)}/files`));setMsg('')}catch(e:any){setMsg(e.message)}}
  return <section className="section"><SectionTitle eyebrow="HISTORY" title="Archives" actions={<div className="inline"><select value={repo} onChange={e=>setRepo(e.target.value)}>{repos.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select><button onClick={()=>void load()}>Load archives</button></div>}/>{msg&&<Notice text={msg}/>}<div className="split"><Card>{archives.length?archives.map(a=><button className={`archiveRow ${selected===a.name?'selected':''}`} onClick={()=>void browse(a.name)} key={a.name}><Archive size={17}/><span><b>{a.name}</b><small>{a.time||''}</small></span></button>):<Empty text="Load a real repository to browse archives."/>}</Card><Card>{files.length?<table><thead><tr><th>Path</th><th>Size</th></tr></thead><tbody>{files.map((f:any,i)=><tr key={i}><td className="path">{f.path}</td><td>{typeof f.size==='number'?formatBytes(f.size):'—'}</td></tr>)}</tbody></table>:<Empty text="Select an archive to browse files."/>}</Card></div></section>
}

function Restore(){
  const [repos,setRepos]=useState<Repository[]>([]); const [repo,setRepo]=useState(''); const [archive,setArchive]=useState(''); const [paths,setPaths]=useState(''); const [target,setTarget]=useState('restored'); const [msg,setMsg]=useState('')
  useEffect(()=>{ void api.get<Repository[]>('/repositories').then(r=>{setRepos(r);if(r[0])setRepo(String(r[0].id))}) },[])
  async function restore(e:React.FormEvent){e.preventDefault();setMsg('Running borg extract…');try{const r:any=await api.post('/restore',{repository_id:Number(repo),archive,paths:paths?paths.split(',').map(x=>x.trim()):[],target});setMsg(`Restore complete: ${r.target}`)}catch(e:any){setMsg(e.message)}}
  return <section className="section"><SectionTitle eyebrow="RECOVERY" title="Restore" subtitle="Restores are constrained to the mounted /restore directory."/><Card><form className="wideForm" onSubmit={restore}><label>Repository<select value={repo} onChange={e=>setRepo(e.target.value)}>{repos.map(r=><option value={r.id} key={r.id}>{r.name}</option>)}</select></label><label>Archive<input value={archive} onChange={e=>setArchive(e.target.value)} required placeholder="backup-2026-09-01_10-00-00"/></label><label>Paths<input value={paths} onChange={e=>setPaths(e.target.value)} placeholder="Optional; comma-separated. Empty = full archive"/></label><label>Target inside /restore<input value={target} onChange={e=>setTarget(e.target.value)} required/></label><button><RotateCcw size={16}/>Run Borg restore</button>{msg&&<Notice text={msg}/>}</form></Card></section>
}

function JobTable({jobs}:{jobs:Job[]}){return <table><thead><tr><th>Archive</th><th>Status</th><th>Started</th><th>Finished</th></tr></thead><tbody>{jobs.map(j=><tr key={j.id}><td>{j.archive_name}</td><td><span className={statusClass(j.status)}>{j.status}</span></td><td>{fmt(j.started_at)}</td><td>{fmt(j.finished_at)}</td></tr>)}</tbody></table>}
function JobRow({job}:{job:Job}){return <div className="listRow"><div className={`statusIcon ${job.status}`}><ServerCog size={18}/></div><div className="grow"><b>{job.archive_name}</b><small>{fmt(job.started_at)} · repo #{job.repository_id}</small></div><span className={statusClass(job.status)}>{job.status}</span></div>}
function Metric({label,value,icon}:{label:string,value:any,icon:any}){return <div className="metric"><div className="metricIcon">{icon}</div><span>{label}</span><strong>{value}</strong></div>}
function Card({children}:{children:React.ReactNode}){return <div className="card">{children}</div>}
function Empty({text}:{text:string}){return <div className="empty"><HardDrive size={28}/><b>{text}</b><span>The workspace is ready for real Borg data.</span></div>}
function Hint({children}:{children:React.ReactNode}){return <div className="hint"><Wrench size={15}/><span>{children}</span></div>}
function Notice({text}:{text:string}){return <div className="notice">{text}</div>}
function SectionTitle({eyebrow,title,subtitle,actions}:{eyebrow:string,title:string,subtitle?:string,actions?:React.ReactNode}){return <div className="sectionTitle"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div>{actions}</div>}
function formatBytes(n:number){if(n<1024)return n+' B';const u=['KB','MB','GB','TB'];let i=-1;do{n/=1024;i++}while(n>=1024&&i<u.length-1);return n.toFixed(1)+' '+u[i]}

createRoot(document.getElementById('root')!).render(<React.StrictMode><BrowserRouter><Layout/></BrowserRouter></React.StrictMode>)
