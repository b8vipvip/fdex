export const projectStatusLabels: Record<string, string> = {
  draft: '待完善', analyzed: '已分析', generated: '已生成方案', active: '进行中', completed: '已完成', failed: '分析失败',
};

export const projectStatusClasses: Record<string, string> = {
  draft: 'bg-amber-50 text-amber-700 ring-amber-200', analyzed: 'bg-blue-50 text-blue-700 ring-blue-200', generated: 'bg-emerald-50 text-emerald-700 ring-emerald-200', active: 'bg-blue-50 text-blue-700 ring-blue-200', completed: 'bg-emerald-50 text-emerald-700 ring-emerald-200', failed: 'bg-red-50 text-red-700 ring-red-200',
};

export const projectTypeLabels: Record<string, string> = {
  general: '通用业务项目', unknown: '待 AI 判断', software: '软件开发', software_development: '软件开发', automation: '自动化流程', office_automation: '办公自动化', data_analysis: '数据分析', consulting: '咨询方案', business_consulting: '业务咨询',
};

export const storageModeLabels: Record<string, string> = { local_only: '仅本地', cloud: '云端', hybrid: '混合模式', temporary: '临时分析' };
export const privacyLabels: Record<string, string> = { public: '公开', normal: '普通', sensitive: '敏感', highly_sensitive: '高度敏感' };
export const fileTypeLabels: Record<string, string> = { text: '文本', image: '图片', spreadsheet: '表格', document: '文档', code: '代码', video: '视频', audio: '音频', archive: '压缩包', unknown: '未知' };
export const assetStatusLabels: Record<string, string> = { uploaded: '未分析', pending: '未分析', analyzing: '分析中', completed: '已完成', analyzed: '已完成', need_user_decision: '需确认', failed: '失败', local_only: '仅本地保存' };
export const assetStatusClasses: Record<string, string> = { uploaded: 'bg-slate-100 text-slate-700', pending: 'bg-slate-100 text-slate-700', analyzing: 'bg-blue-50 text-blue-700', completed: 'bg-emerald-50 text-emerald-700', analyzed: 'bg-emerald-50 text-emerald-700', need_user_decision: 'bg-amber-100 text-amber-800', failed: 'bg-red-50 text-red-700', local_only: 'bg-slate-100 text-slate-700' };

export const reportLabels: Record<string, { title: string; description: string }> = {
  comprehensive_analysis: { title: '项目综合分析报告', description: '汇总项目目标、资料发现、执行建议和关键风险。' },
  requirement_analysis: { title: '需求分析报告', description: '梳理业务需求、目标用户、使用场景和验收标准。' },
  technical_solution: { title: '技术方案', description: '说明系统架构、技术选型和落地路径。' },
  developer_prompt: { title: '开发提示词', description: '可直接交给 AI 或开发团队使用的实施提示词。' },
  industry_solution: { title: '行业解决方案', description: '结合行业特点给出的业务落地建议。' },
  sop: { title: '执行 SOP', description: '按步骤整理的执行流程和协作规范。' },
  risk_report: { title: '风险分析报告', description: '识别项目风险并提供应对建议。' },
  prd: { title: 'PRD 产品需求文档', description: '面向产品和研发团队的完整需求文档。' },
};

export function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
