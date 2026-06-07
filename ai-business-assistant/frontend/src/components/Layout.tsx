import type {ReactNode} from 'react';
import {Link,NavLink,useLocation} from 'react-router-dom';
import {useAuthStore} from '../store/authStore';
import BackButton from './BackButton';

const tabs=[['💬','消息','/messages'],['📁','项目','/projects'],['🧭','发现','/discover'],['👤','我的','/me']];
const primaryPaths=new Set(tabs.map(([, ,path])=>path));

export default function Layout({children,title,back=false,hideTabs=false,actions}:{children:ReactNode;title?:string;back?:boolean;hideTabs?:boolean;actions?:ReactNode}){
  const {pathname}=useLocation();
  const company=useAuthStore(s=>s.user?.company_name);
  const isPrimary=primaryPaths.has(pathname);
  const showTabs=isPrimary&&!hideTabs;
  const showBack=back||!isPrimary;
  return <div className="min-h-[100dvh] w-full bg-slate-100">
    <aside className="fixed inset-y-0 left-0 hidden w-60 border-r bg-white p-5 lg:block">
      <Link to="/messages" className="text-xl font-bold">{company||'AI 虚拟公司'}</Link>
      <nav className="mt-8 space-y-2">{tabs.map(([icon,label,to])=><NavLink key={to} to={to} className={({isActive})=>`flex min-h-12 items-center gap-3 rounded-xl px-4 font-medium ${isActive?'bg-emerald-50 text-emerald-700':'text-slate-600'}`}>{icon}<span>{label}</span></NavLink>)}</nav>
    </aside>
    <div className="w-full min-w-0 lg:pl-60">
      <header className="sticky top-0 z-30 grid min-h-14 w-full grid-cols-[minmax(44px,1fr)_minmax(0,3fr)_minmax(44px,1fr)] items-center border-b bg-white/95 px-2 backdrop-blur lg:px-8">
        <div className="flex justify-start">{showBack&&<BackButton/>}</div>
        <h1 className="min-w-0 truncate text-center text-base font-semibold sm:text-lg" title={title||company||'AI 虚拟公司'}>{title||company||'AI 虚拟公司'}</h1>
        <div className="flex min-w-11 justify-end">{actions}</div>
      </header>
      <main className={`mx-auto w-full min-w-0 max-w-none px-3 py-4 sm:px-6 lg:max-w-5xl lg:py-7 lg:pb-8 ${showTabs?'pb-24':'pb-4'}`}>{children}</main>
    </div>
    {showTabs&&<nav className="fixed inset-x-0 bottom-0 z-50 grid min-h-[72px] grid-cols-4 border-t bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">{tabs.map(([icon,label,to])=><NavLink key={to} to={to} className={({isActive})=>`flex min-h-[64px] flex-col items-center justify-center gap-0.5 text-[13px] ${isActive?'text-emerald-600':'text-slate-500'}`}><span className="text-xl">{icon}</span><span>{label}</span></NavLink>)}</nav>}
  </div>;
}
