import { API_BASE_URL, apiClient } from './client';
import type { Report } from '../types';
export const getReport = (id: number) => apiClient.get<Report>(`/reports/${id}`).then(r => r.data);
export const exportReportUrl = (id: number) => `${API_BASE_URL}/reports/${id}/export-md`;
export async function downloadReport(report: Pick<Report, 'id' | 'report_type'>) { const token = localStorage.getItem('token'); const response = await fetch(exportReportUrl(report.id), { headers: token ? { Authorization: `Bearer ${token}` } : {} }); if (!response.ok) throw new Error('Markdown 导出失败'); const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `${report.report_type}.md`; a.click(); URL.revokeObjectURL(url); }
