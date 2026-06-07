import { Link } from 'react-router-dom';
import { downloadReport } from '../api/documents';
import type { Report } from '../types';
import { formatDate, reportLabels } from '../utils/display';
import EmptyState from './EmptyState';
export default function ReportList({ reports, onGenerate, busy }: { reports: Report[]; onGenerate: () => void; busy?: boolean }) {
  if (!reports.length) return <EmptyState icon="📄" title="还没有生成方案" description="请先让 AI 分析项目，系统会生成可执行方案。" action={<button disabled={busy} onClick={onGenerate} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-60">{busy ? '正在生成...' : '生成方案文档'}</button>} />;
  return <div className="space-y-3">{reports.map(report => { const info = reportLabels[report.report_type] ?? { title: report.title, description: 'AI 为当前项目生成的方案文档。' }; return <article key={report.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"><div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><h3 className="font-semibold text-slate-900">{info.title}</h3><p className="mt-1 text-sm text-slate-500">{info.description}</p><p className="mt-2 text-xs text-slate-400">更新于 {formatDate(report.updated_at)}</p></div><div className="grid shrink-0 grid-cols-3 gap-2 sm:flex"><Link to={`/reports/${report.id}`} className="rounded-lg bg-blue-600 px-3 py-2 text-center text-sm text-white">查看</Link><button onClick={() => void navigator.clipboard.writeText(report.content_markdown)} className="rounded-lg border px-3 py-2 text-sm text-slate-700">复制</button><button onClick={() => void downloadReport(report)} className="rounded-lg border px-3 py-2 text-sm text-slate-700">导出 Markdown</button></div></div></article>; })}</div>;
}
