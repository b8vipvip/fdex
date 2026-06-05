import type { Report } from '../types';
const renderMarkdown = (md: string) => md.split('\n').map((line, i) => {
  if (line.startsWith('# ')) return <h1 key={i}>{line.slice(2)}</h1>;
  if (line.startsWith('## ')) return <h2 key={i}>{line.slice(3)}</h2>;
  if (line.startsWith('- ')) return <li key={i}>{line.slice(2)}</li>;
  if (!line.trim()) return <br key={i} />;
  return <p key={i}>{line}</p>;
});
export default function DocumentViewer({ report }: { report: Report }) {
  return <article className="prose-lite rounded-2xl bg-white p-5 shadow-sm">{renderMarkdown(report.content_markdown)}</article>;
}
