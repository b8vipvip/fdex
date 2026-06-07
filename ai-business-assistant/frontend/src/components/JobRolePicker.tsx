import {useEffect,useMemo,useRef,useState} from 'react';
import {getJobRoles} from '../api/employees';
import type {JobRole} from '../types';

export default function JobRolePicker({value,onChange,disabled=false}:{value?:number;onChange:(x:JobRole)=>void;disabled?:boolean}){
  const [open,setOpen]=useState(false),[q,setQ]=useState(''),[industry,setIndustry]=useState('');const [rows,setRows]=useState<JobRole[]>([]);const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{getJobRoles().then(setRows)},[]);
  useEffect(()=>{const close=(e:PointerEvent)=>{if(!root.current?.contains(e.target as Node))setOpen(false)};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close)},[]);
  const selected=rows.find(x=>x.id===value);const industries=useMemo(()=>[...new Set(rows.map(x=>x.industry))],[rows]);
  const filtered=rows.filter(x=>(!industry||x.industry===industry)&&(!q.trim()||`${x.title} ${x.aliases} ${x.description} ${x.category}`.toLowerCase().includes(q.trim().toLowerCase())));const categories=[...new Set(filtered.map(x=>x.category))];
  return <div ref={root} className="relative">
    <button type="button" disabled={disabled} onClick={()=>setOpen(!open)} className={`flex w-full items-center justify-between rounded-xl border bg-white px-3 text-left ${open?'border-emerald-500 ring-2 ring-emerald-100':''}`}><span className={selected?'text-slate-900':'text-slate-400'}>{selected?`${selected.industry} · ${selected.title}`:'请选择行业职位'}</span><span className={`text-slate-400 ${open?'rotate-180':''}`}>⌄</span></button>
    {open&&<div className="absolute left-0 right-0 top-[calc(100%+8px)] z-50 max-h-[60vh] overflow-hidden rounded-2xl border bg-white shadow-2xl">
      <div className="border-b p-3"><input autoFocus value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索职位，如 AI、法务、运营、工程师" className="w-full rounded-xl border bg-slate-50 px-3"/><div className="mt-3 flex gap-2 overflow-x-auto pb-1"><button type="button" onClick={()=>setIndustry('')} className={`shrink-0 rounded-full px-3 text-sm ${!industry?'bg-emerald-600 text-white':'bg-slate-100'}`}>全部</button>{industries.map(x=><button type="button" key={x} onClick={()=>setIndustry(x)} className={`shrink-0 rounded-full px-3 text-sm ${industry===x?'bg-emerald-600 text-white':'bg-slate-100'}`}>{x}</button>)}</div></div>
      <div className="max-h-[calc(60vh-116px)] overflow-y-auto p-3">{categories.map(category=><section key={category} className="mb-4 last:mb-0"><h3 className="mb-2 text-xs font-semibold text-slate-400">{category}</h3><div className="space-y-2">{filtered.filter(x=>x.category===category).map(x=><div key={x.id} className={`flex items-center gap-3 rounded-xl border p-3 ${value===x.id?'border-emerald-500 bg-emerald-50':'border-slate-200'}`}><div className="min-w-0 flex-1"><p className="font-medium">{x.title}</p><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{x.description}</p></div><button type="button" onClick={()=>{onChange(x);setOpen(false);setQ('');setIndustry(x.industry)}} className={`shrink-0 rounded-lg px-3 text-sm font-medium ${value===x.id?'bg-white text-emerald-700':'bg-emerald-600 text-white'}`}>{value===x.id?'已选择':'选择'}</button></div>)}</div></section>)}{!filtered.length&&<p className="py-8 text-center text-sm text-slate-500">没有找到匹配职位</p>}</div>
    </div>}
  </div>;
}
