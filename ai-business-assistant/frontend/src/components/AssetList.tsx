import type { Asset } from '../types';
export default function AssetList({ assets, onAnalyze, busyId }: { assets: Asset[]; onAnalyze: (id: number) => void; busyId?: number }) {
  if (!assets.length) return <p className="rounded-xl bg-white p-4 text-sm text-slate-500">还没有上传资料。</p>;
  return <div className="space-y-3">{assets.map(a => <div key={a.id} className="flex flex-col gap-3 rounded-xl border bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
    <div><div className="font-medium">{a.original_filename}</div><div className="text-sm text-slate-500">类型：{a.file_type} · MIME：{a.mime_type} · 状态：{a.status}</div></div>
    <button disabled={busyId === a.id} onClick={() => onAnalyze(a.id)} className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-60">{busyId === a.id ? '分析中...' : '分析资料'}</button>
  </div>)}</div>;
}
