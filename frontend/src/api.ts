export type Repository = { id:number; name:string; location:string; created_at:string }
export type Job = { id:number; repository_id:number; archive_name:string; status:string; return_code:number|null; log:string; started_at:string|null; finished_at:string|null }
export type Health = { ok:boolean; mock_borg:boolean; borg_available:boolean; scheduler_running:boolean; timezone:string }
export type Schedule = { id:number; name:string; repository_id:number; cron:string; sources:string[]; compression:string; excludes:string[]; enabled:boolean; next_run_at:string|null }
export type ArchiveMetric = { name:string; time:string|null; duration:number; nfiles:number; original_size:number; compressed_size:number; deduplicated_size:number }
export type RepositoryMetric = {
  id:number; name:string; location:string; ok:boolean; error:string|null; archive_count:number; last_archive_at:string|null;
  logical_size:number; compressed_size:number; deduplicated_size:number; physical_size:number|null; disk_total:number|null; disk_free:number|null;
  dedup_ratio:number|null; compression_ratio:number|null; unique_chunks:number|null; total_chunks:number|null; history:ArchiveMetric[]
}
export type Metrics = { repositories:RepositoryMetric[]; total_repository_size:number; total_archives:number; updated_at:string }

async function errorText(r:Response){
  const text=await r.text()
  try { const j=JSON.parse(text); return j.detail || text } catch { return text }
}

export const api = {
  get: async <T,>(path:string):Promise<T> => {
    const r=await fetch('/api'+path)
    if(!r.ok) throw new Error(await errorText(r))
    return r.json()
  },
  post: async <T,>(path:string, body?:unknown):Promise<T> => {
    const r=await fetch('/api'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined})
    if(!r.ok) throw new Error(await errorText(r))
    return r.json()
  },
  del: async (path:string):Promise<void> => {
    const r=await fetch('/api'+path,{method:'DELETE'})
    if(!r.ok) throw new Error(await errorText(r))
  }
}
