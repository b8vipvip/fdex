import type { ReactNode } from 'react';
export default function EmptyState({ icon = '✨', title, description, action }: { icon?: string; title: string; description: string; action?: ReactNode }) {
  return <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-5 py-8 text-center">
    <div className="text-3xl" aria-hidden>{icon}</div><h3 className="mt-3 font-semibold text-slate-800">{title}</h3><p className="mx-auto mt-1 max-w-xl text-sm leading-6 text-slate-500">{description}</p>{action && <div className="mt-4">{action}</div>}
  </div>;
}
