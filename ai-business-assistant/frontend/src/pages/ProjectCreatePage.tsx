import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Layout from '../components/Layout';
import { createProject } from '../api/projects';
import type { ProfessionalLevel, RetentionPolicy, StorageMode } from '../types';

const storageModeOptions: { value: StorageMode; title: string; desc: string }[] = [
  { value: 'hybrid', title: '混合模式（推荐）', desc: '敏感资料先提醒，可选择脱敏、临时分析或仅本地保存。' },
  { value: 'local_only', title: '本地模式', desc: '资料只保存在当前设备，第一版 Web MVP 不进行云端同步。' },
  { value: 'cloud', title: '云端模式', desc: '资料保存云端，支持多端同步和完整 AI 分析。' },
  { value: 'temporary', title: '临时分析模式', desc: '资料上传后仅用于本次分析，完成后删除原始文件。' },
];

export default function ProjectCreatePage() {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [professional_level, setLevel] = useState<ProfessionalLevel>('business');
  const [storage_mode, setStorageMode] = useState<StorageMode>('hybrid');
  const [data_retention_policy, setRetention] = useState<RetentionPolicy>('keep_forever');
  const [allow_third_party_ai, setAllowThirdPartyAI] = useState(true);
  const [auto_desensitize, setAutoDesensitize] = useState(true);
  const nav = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    const p = await createProject({ title, description, professional_level, storage_mode, data_retention_policy, allow_third_party_ai, auto_desensitize });
    nav(`/projects/${p.id}`);
  }

  return <Layout><div className="mx-auto max-w-3xl rounded-3xl bg-white p-6 shadow-sm"><h1 className="text-2xl font-bold">新建项目</h1><form onSubmit={submit} className="mt-6 space-y-5">
    <input className="w-full rounded-xl border p-3" placeholder="项目标题" value={title} onChange={e => setTitle(e.target.value)} />
    <textarea className="min-h-40 w-full rounded-xl border p-3" placeholder="用大白话描述需求：你想解决什么问题？现在怎么做？希望 AI 或系统帮你做什么？" value={description} onChange={e => setDescription(e.target.value)} />
    <select className="w-full rounded-xl border p-3" value={professional_level} onChange={e => setLevel(e.target.value as ProfessionalLevel)}><option value="beginner">完全小白</option><option value="business">懂业务不懂技术</option><option value="product">产品/项目经理</option><option value="developer">技术人员</option><option value="auto">AI自动判断</option></select>
    <section className="rounded-2xl border bg-slate-50 p-4"><h2 className="font-semibold">数据存储方式</h2><div className="mt-3 grid gap-3 md:grid-cols-2">{storageModeOptions.map(opt => <label key={opt.value} className={`cursor-pointer rounded-xl border p-3 ${storage_mode === opt.value ? 'border-blue-500 bg-blue-50' : 'bg-white'}`}><input type="radio" className="mr-2" checked={storage_mode === opt.value} onChange={() => setStorageMode(opt.value)} /><span className="font-medium">{opt.title}</span><p className="mt-1 text-xs text-slate-500">{opt.desc}</p></label>)}</div>{storage_mode === 'local_only' && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-700">本地模式暂使用浏览器本地存储占位，换设备不同步；如需云端 AI 分析，请选择临时上传或脱敏上传。</p>}</section>
    <section className="grid gap-3 rounded-2xl border p-4 md:grid-cols-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allow_third_party_ai} onChange={e => setAllowThirdPartyAI(e.target.checked)} />允许第三方 AI 分析</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={auto_desensitize} onChange={e => setAutoDesensitize(e.target.checked)} />自动脱敏后再分析</label><label className="text-sm md:col-span-2">原始文件保留时间<select className="mt-2 w-full rounded-xl border p-3" value={data_retention_policy} onChange={e => setRetention(e.target.value as RetentionPolicy)}><option value="keep_forever">长期保留</option><option value="delete_after_analysis">分析后删除</option><option value="delete_after_1_day">1 天后删除</option><option value="delete_after_7_days">7 天后删除</option><option value="delete_after_30_days">30 天后删除</option></select></label></section>
    <button className="rounded-xl bg-blue-600 px-5 py-3 text-white">创建项目</button>
  </form></div></Layout>;
}
