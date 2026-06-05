import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Layout from '../components/Layout';
import ProjectCard from '../components/ProjectCard';
import { getProjects } from '../api/projects';
import type { Project } from '../types';
export default function DashboardPage(){const [projects,setProjects]=useState<Project[]>([]);useEffect(()=>{getProjects().then(setProjects)},[]);return <Layout><div className="mb-6 flex items-center justify-between"><div><h1 className="text-2xl font-bold">项目空间</h1><p className="text-slate-500">PC 和手机浏览器登录同一账号即可同步项目数据。</p></div><Link className="rounded-xl bg-blue-600 px-4 py-2 text-white" to="/projects/new">新建项目</Link></div><div className="grid gap-4 md:grid-cols-2">{projects.map(p=><ProjectCard key={p.id} project={p}/>)}</div>{!projects.length&&<div className="rounded-2xl bg-white p-8 text-center text-slate-500">暂无项目，点击右上角新建。</div>}</Layout>}
