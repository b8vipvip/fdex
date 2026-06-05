import { Link } from 'react-router-dom';
import type { Project } from '../types';
export default function ProjectCard({ project }: { project: Project }) {
  return <Link to={`/projects/${project.id}`} className="block rounded-2xl border bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
    <div className="flex items-start justify-between gap-3"><h3 className="font-semibold">{project.title}</h3><span className="rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700">{project.status}</span></div>
    <p className="mt-2 line-clamp-2 text-sm text-slate-600">{project.description || '暂无描述'}</p>
    <div className="mt-4 grid grid-cols-3 gap-2 text-xs text-slate-500"><span>类型：{project.project_type}</span><span>完整度：{Math.round(project.requirement_score)}%</span><span>更新：{new Date(project.updated_at).toLocaleDateString()}</span></div>
  </Link>;
}
