import type { ReactNode } from 'react';
import { FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';
export default function LoginPage() { const [email,setEmail]=useState(''); const [password,setPassword]=useState(''); const [err,setErr]=useState(''); const nav=useNavigate(); const login=useAuthStore(s=>s.login);
  async function submit(e: FormEvent){e.preventDefault(); setErr(''); try{await login(email,password); nav('/');}catch{setErr('登录失败，请检查邮箱和密码');}}
  return <AuthShell title="登录"><form onSubmit={submit} className="space-y-4"><input className="w-full rounded-xl border p-3" placeholder="邮箱" value={email} onChange={e=>setEmail(e.target.value)} /><input type="password" className="w-full rounded-xl border p-3" placeholder="密码" value={password} onChange={e=>setPassword(e.target.value)} />{err&&<p className="text-sm text-red-600">{err}</p>}<button className="w-full rounded-xl bg-blue-600 p-3 text-white">登录</button><p className="text-center text-sm">还没有账号？<Link className="text-blue-600" to="/register">去注册</Link></p></form></AuthShell>;}
export function AuthShell({title,children}:{title:string;children:ReactNode}){return <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 p-4"><div className="w-full max-w-md rounded-3xl bg-white p-8 shadow-xl"><h1 className="mb-2 text-2xl font-bold">AI业务落地助手</h1><p className="mb-6 text-slate-500">{title}后开始把大白话需求转成可执行方案</p>{children}</div></main>}
