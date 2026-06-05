import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import Layout from '../components/Layout';
import DocumentViewer from '../components/DocumentViewer';
import { exportReportUrl, getReport } from '../api/documents';
import type { Report } from '../types';
export default function DocumentPage(){const id=Number(useParams().id);const [report,setReport]=useState<Report>();useEffect(()=>{getReport(id).then(setReport)},[id]);async function copy(){if(report) await navigator.clipboard.writeText(report.content_markdown);}function download(){const token=localStorage.getItem('token');fetch(exportReportUrl(id),{headers:{Authorization:`Bearer ${token}`}}).then(r=>r.blob()).then(blob=>{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${report?.report_type||'report'}.md`;a.click();});}return <Layout>{report&&<><div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h1 className="text-2xl font-bold">{report.title}</h1><p className="text-sm text-slate-500">Markdown 文档，可复制或导出</p></div><div className="flex gap-2"><button onClick={copy} className="rounded-xl bg-slate-900 px-4 py-2 text-white">复制内容</button><button onClick={download} className="rounded-xl bg-blue-600 px-4 py-2 text-white">导出 Markdown</button></div></div><DocumentViewer report={report}/></>}</Layout>}
