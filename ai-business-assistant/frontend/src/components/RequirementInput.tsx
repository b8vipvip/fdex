import { useState } from 'react';
export default function RequirementInput({ onSubmit }: { onSubmit: (text: string) => Promise<void> }) {
  const [text, setText] = useState(''); const [loading, setLoading] = useState(false);
  return <div className="rounded-2xl bg-white p-4 shadow-sm"><textarea className="min-h-28 w-full rounded-xl border p-3 outline-none focus:ring-2 focus:ring-blue-200" placeholder="继续用大白话补充你的需求、目标、限制或想法..." value={text} onChange={e => setText(e.target.value)} />
    <div className="mt-3 text-right"><button className="rounded-lg bg-slate-900 px-4 py-2 text-white disabled:opacity-50" disabled={!text.trim() || loading} onClick={async () => { setLoading(true); await onSubmit(text); setText(''); setLoading(false); }}>{loading ? '保存中...' : '补充需求'}</button></div></div>;
}
