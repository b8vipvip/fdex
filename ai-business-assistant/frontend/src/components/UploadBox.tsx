export default function UploadBox({ onUpload, loading }: { onUpload: (file: File) => void; loading?: boolean }) {
  return <label className="flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50/60 p-6 text-center hover:bg-blue-50">
    <input type="file" className="hidden" onChange={e => e.target.files?.[0] && onUpload(e.target.files[0])} />
    <div className="text-3xl">📎</div><div className="mt-2 font-medium">{loading ? '上传中...' : '点击上传资料'}</div>
    <p className="mt-1 text-sm text-slate-500">支持文本、图片、PDF、Word、Excel、音频、视频、代码文件，单文件 50MB 内</p>
  </label>;
}
