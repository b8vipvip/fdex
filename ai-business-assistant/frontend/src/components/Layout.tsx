import type { ReactNode } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuthStore(); const navigate = useNavigate();
  return <div className="min-h-screen">
    <header className="sticky top-0 z-10 border-b bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <Link to="/" className="text-lg font-bold">AI业务落地助手</Link>
        <nav className="flex items-center gap-3 text-sm">
          <NavLink className="hover:text-blue-600" to="/">项目</NavLink><NavLink className="hover:text-blue-600" to="/settings">设置</NavLink>
          <span className="hidden sm:inline text-slate-500">{user?.name}</span>
          <button className="rounded-lg bg-slate-900 px-3 py-1.5 text-white" onClick={() => { logout(); navigate('/login'); }}>退出</button>
        </nav>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
  </div>;
}
