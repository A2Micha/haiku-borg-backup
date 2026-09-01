export type Repository = { id:number; name:string; location:string; created_at:string }
export type Job = { id:number; repository_id:number; archive_name:string; status:string; return_code:number|null; log:string; started_at:string|null; finished_at:string|null }
export const api = {
  get: async <T,>(path:string):Promise<T> => { const r=await fetch('/api'+path); if(!r.ok) throw new Error(await r.text()); return r.json() },
  post: async <T,>(path:string, body?:unknown):Promise<T> => { const r=await fetch('/api'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}); if(!r.ok) throw new Error(await r.text()); return r.json() },
  del: async (path:string) => { const r=await fetch('/api'+path,{method:'DELETE'}); if(!r.ok) throw new Error(await r.text()) }
}
