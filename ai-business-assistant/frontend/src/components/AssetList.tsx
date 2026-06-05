import type { Asset, PrivacyDecision } from '../types';

const decisionLabels: { decision: PrivacyDecision; label: string }[] = [
  { decision: 'desensitize', label: '自动脱敏后分析' },
  { decision: 'temporary', label: '临时上传分析后删除' },
  { decision: 'local_only', label: '仅本地保存' },
  { decision: 'confirm_upload', label: '确认继续上传原文件' },
];

export default function AssetList({ assets, onAnalyze, onPrivacyDecision, busyId }: { assets: Asset[]; onAnalyze: (id: number) => void; onPrivacyDecision?: (id: number, decision: PrivacyDecision) => void; busyId?: number }) {
  if (!assets.length) return <p className="rounded-xl bg-white p-4 text-sm text-slate-500">还没有上传资料。</p>;
  return <div className="space-y-3">{assets.map(a => <div key={a.id} className="flex flex-col gap-3 rounded-xl border bg-white p-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><div className="font-medium">{a.original_filename}</div><div className="text-sm text-slate-500">类型：{a.file_type} · MIME：{a.mime_type} · 状态：{a.status}</div><div className="mt-1 text-xs text-slate-500">隐私级别：{a.privacy_level} · {a.is_sensitive ? '可能包含敏感信息' : '未检测到敏感信息'}{a.original_deleted_at ? ' · 原始文件已删除' : ''}</div></div>
    <button disabled={busyId === a.id || a.status === 'need_user_decision' || a.status === 'local_only'} onClick={() => onAnalyze(a.id)} className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-60">{busyId === a.id ? '分析中...' : '分析资料'}</button></div>
    {a.status === 'need_user_decision' && <div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800"><div className="font-semibold">检测到该文件可能包含敏感信息，请选择处理方式：</div><div className="mt-2 flex flex-wrap gap-2">{decisionLabels.map(item => <button key={item.decision} onClick={() => onPrivacyDecision?.(a.id, item.decision)} className="rounded-lg bg-white px-3 py-1 text-xs text-amber-800 shadow-sm hover:bg-amber-100">{item.label}</button>)}</div></div>}
  </div>)}</div>;
}
