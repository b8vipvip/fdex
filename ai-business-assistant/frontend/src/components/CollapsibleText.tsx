import { useState } from 'react';
export default function CollapsibleText({ text, lines = 3, className = '' }: { text: string; lines?: number; className?: string }) {
  const [open, setOpen] = useState(false); if (!text) return <p className={className}>暂无描述</p>;
  const needsToggle = text.length > 90;
  return <div className={className}><p className={!open ? (lines === 3 ? 'line-clamp-3' : 'line-clamp-2') : ''}>{text}</p>{needsToggle && <button type="button" onClick={() => setOpen(v => !v)} className="mt-2 text-sm font-medium text-blue-600 hover:text-blue-700">{open ? '收起' : '展开全文'}</button>}</div>;
}
