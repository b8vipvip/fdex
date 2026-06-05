import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Layout from '../components/Layout';
import UploadBox from '../components/UploadBox';
import AssetList from '../components/AssetList';
import AnalysisStatus from '../components/AnalysisStatus';
import RequirementInput from '../components/RequirementInput';
import { addMessage, analyzeProject, getMessages, getPrivacySummary, getProject, getReports } from '../api/projects';
import { analyzeAsset, createLocalOnlyAsset, decideAssetPrivacy, getAssets, uploadAsset } from '../api/assets';
import type { AnalysisResult, Asset, Message, PrivacyDecision, PrivacySummary, Project, Report } from '../types';

const privacyTypeNames: Record<string, string> = {
  phone: '手机号', email: '邮箱', api_key: 'API Key', token: 'Token', cookie: 'Cookie', password: '密码', secret: 'Secret', access_key: 'Access Key', private_key: '私钥', id_card: '身份证号', bank_card: '银行卡号', address: '地址关键词', contract: '合同关键词', finance: '财务关键词', customer_list: '客户名单关键词'
};

const modeLabel: Record<string, string> = { local_only: '本地模式', cloud: '云端模式', hybrid: '混合模式', temporary: '临时分析模式' };

function SensitiveDialog({ asset, onDecision, onClose }: { asset: Asset; onDecision: (decision: PrivacyDecision) => void; onClose: () => void }) {
  const items = asset.privacy_detection?.detected_items ?? [];
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl"><div className="flex items-start justify-between gap-3"><h2 className="text-lg font-bold">检测到该文件可能包含敏感信息</h2><button onClick={onClose} className="text-slate-400">✕</button></div><p className="mt-2 text-sm text-slate-600">文件：{asset.original_filename}</p><ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">{items.length ? items.map(item => <li key={item.type}>{privacyTypeNames[item.type] ?? item.type}（{item.count} 处）</li>) : <li>敏感关键词或隐私信息</li>}</ul><div className="mt-5 grid gap-2"><button onClick={() => onDecision('desensitize')} className="rounded-xl bg-blue-600 px-4 py-2 text-white">1. 自动脱敏后分析</button><button onClick={() => onDecision('temporary')} className="rounded-xl bg-indigo-600 px-4 py-2 text-white">2. 临时上传分析后删除</button><button onClick={() => onDecision('local_only')} className="rounded-xl bg-slate-700 px-4 py-2 text-white">3. 仅本地保存</button><button onClick={() => onDecision('confirm_upload')} className="rounded-xl border px-4 py-2 text-slate-700">4. 我确认继续上传原文件</button></div></div></div>;
}

export default function ProjectDetailPage() {
  const id = Number(useParams().id);
  const [project, setProject] = useState<Project>();
  const [privacy, setPrivacy] = useState<PrivacySummary>();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisResult[]>([]);
  const [busyAsset, setBusyAsset] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sensitiveAsset, setSensitiveAsset] = useState<Asset>();

  const refresh = async () => { setProject(await getProject(id)); setAssets(await getAssets(id)); setReports(await getReports(id)); setMessages(await getMessages(id)); setPrivacy(await getPrivacySummary(id)); };
  useEffect(() => { void refresh(); }, [id]);

  async function handleUpload(file: File) {
    setUploading(true);
    try {
      if (project?.storage_mode === 'local_only') {
        const key = `fdex-local-assets-${id}`;
        const oldItems = JSON.parse(localStorage.getItem(key) || '[]');
        localStorage.setItem(key, JSON.stringify([{ name: file.name, size: file.size, saved_at: new Date().toISOString() }, ...oldItems]));
        const uploaded = await createLocalOnlyAsset(id, file);
        if (uploaded.is_sensitive) setSensitiveAsset(uploaded);
        await refresh();
        return;
      }
      const uploaded = await uploadAsset(id, file);
      if (uploaded.status === 'need_user_decision' || uploaded.is_sensitive) setSensitiveAsset(uploaded);
      await refresh();
    } finally {
      setUploading(false);
    }
  }

  async function handlePrivacyDecision(assetId: number, decision: PrivacyDecision) {
    await decideAssetPrivacy(assetId, decision);
    setSensitiveAsset(undefined);
    await refresh();
  }

  return <Layout>{project && <><div className="grid gap-4 md:grid-cols-[1fr_260px]"><section className="rounded-2xl bg-white p-5 shadow-sm"><div className="flex items-start justify-between gap-4"><div><h1 className="text-2xl font-bold">{project.title}</h1><p className="mt-2 text-slate-600">{project.description}</p><p className="mt-3 text-sm text-slate-500">项目类型：{project.project_type}</p><p className="mt-1 text-sm text-blue-700">隐私模式：{modeLabel[project.storage_mode]} · 第三方 AI：{project.allow_third_party_ai ? '允许' : '不允许'} · 自动脱敏：{project.auto_desensitize ? '开启' : '关闭'}</p>{project.storage_mode === 'local_only' && <p className="mt-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-700">本地模式暂使用浏览器本地存储，换设备不同步；上传资料不会保存原始文件到云端。</p>}</div><button disabled={busy} onClick={async () => { setBusy(true); try { const rs = await analyzeProject(id); setReports(rs); await refresh(); } finally { setBusy(false); } }} className="rounded-xl bg-green-600 px-4 py-2 text-white disabled:opacity-60">{busy ? '综合分析中...' : '综合分析项目'}</button></div></section><AnalysisStatus status={project.status} score={project.requirement_score} /></div>
  {privacy && <section className="mt-4 grid gap-3 rounded-2xl bg-white p-4 text-sm shadow-sm md:grid-cols-4"><div><div className="text-slate-500">存储模式</div><div className="font-semibold">{modeLabel[privacy.storage_mode]}</div></div><div><div className="text-slate-500">敏感文件</div><div className="font-semibold">{privacy.sensitive_assets}/{privacy.total_assets}</div></div><div><div className="text-slate-500">待处理</div><div className="font-semibold">{privacy.pending_decision_assets}</div></div><div><div className="text-slate-500">保留策略</div><div className="font-semibold">{privacy.data_retention_policy}</div></div></section>}
  <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_1fr]"><div className="space-y-6"><RequirementInput onSubmit={async (text) => { await addMessage(id, text); await refresh(); }} /><UploadBox loading={uploading} onUpload={handleUpload} /><section><h2 className="mb-3 font-semibold">已上传资料</h2><AssetList assets={assets} busyId={busyAsset} onPrivacyDecision={handlePrivacyDecision} onAnalyze={async (assetId) => { setBusyAsset(assetId); try { const res = await analyzeAsset(assetId); setAnalysis(prev => [res, ...prev]); await refresh(); } finally { setBusyAsset(undefined); } }} /></section></div>
  <div className="space-y-6"><section className="rounded-2xl bg-white p-4 shadow-sm"><h2 className="font-semibold">补充需求记录</h2><div className="mt-3 max-h-52 space-y-2 overflow-auto">{messages.map(m => <p key={m.id} className="rounded-lg bg-slate-50 p-2 text-sm">{m.content}</p>)}</div></section><section className="rounded-2xl bg-white p-4 shadow-sm"><h2 className="font-semibold">本次资料分析结果</h2><div className="mt-3 space-y-3">{analysis.map(a => <div key={a.id} className="rounded-lg bg-slate-50 p-3 text-sm"><div className="font-medium">{a.analyzer_type}</div><pre className="mt-2 max-h-40 whitespace-pre-wrap overflow-auto text-xs text-slate-600">{a.summary}</pre></div>)}</div></section><section className="rounded-2xl bg-white p-4 shadow-sm"><div className="flex items-center justify-between"><h2 className="font-semibold">报告列表</h2><button disabled={busy} onClick={async () => { setBusy(true); try { const rs = await analyzeProject(id); setReports(rs); await refresh(); } finally { setBusy(false); } }} className="text-sm text-blue-600">生成文档</button></div><div className="mt-3 space-y-2">{reports.map(r => <Link key={r.id} to={`/reports/${r.id}`} className="block rounded-lg border p-3 hover:bg-slate-50"><div className="font-medium">{r.title}</div><div className="text-xs text-slate-500">{r.report_type}</div></Link>)}</div></section></div></div>{sensitiveAsset && <SensitiveDialog asset={sensitiveAsset} onClose={() => setSensitiveAsset(undefined)} onDecision={(decision) => void handlePrivacyDecision(sensitiveAsset.id, decision)} />}</>}</Layout>;
}
