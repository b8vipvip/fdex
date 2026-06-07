import {type ChangeEvent,FormEvent,useRef,useState} from 'react';
import ChatPlusPanel,{type ChatPlusAction} from './ChatPlusPanel';

export default function ChatInputBar({text,onTextChange,onSend,onAttach,plusOpen,onPlusChange,placeholder='给员工安排任务...',onToast}:{text:string;onTextChange:(x:string)=>void;onSend:()=>void;onAttach:(file:File)=>void;plusOpen:boolean;onPlusChange:(x:boolean)=>void;placeholder?:string;onToast:(x:string)=>void}){
  const [voice,setVoice]=useState(false);
  const gallery=useRef<HTMLInputElement>(null),camera=useRef<HTMLInputElement>(null),file=useRef<HTMLInputElement>(null),video=useRef<HTMLInputElement>(null);
  function submit(e:FormEvent){e.preventDefault();onSend()}
  function action(x:ChatPlusAction){
    if(x==='gallery')gallery.current?.click();else if(x==='camera')camera.current?.click();else if(x==='file')file.current?.click();else if(x==='video')video.current?.click();
    else if(x==='call')onToast('语音通话功能即将上线');else if(x==='transcribe')onToast('语音转文字功能即将上线');else onToast(`${x==='project'?'工作':'更多'}功能即将上线`);
  }
  const pick=(e:ChangeEvent<HTMLInputElement>)=>{const picked=e.target.files?.[0];if(picked)onAttach(picked);e.target.value=''};
  return <form onSubmit={submit} className="safe-bottom fixed inset-x-0 bottom-0 z-40 border-t bg-white shadow-[0_-4px_18px_rgba(15,23,42,0.06)] lg:left-60">
    {plusOpen&&<ChatPlusPanel onAction={action}/>} 
    <div className="mx-auto flex max-w-5xl items-center gap-2 p-2">
      <button type="button" onClick={()=>setVoice(!voice)} aria-label={voice?'切换到文字输入':'切换到语音输入'} className="h-11 w-11 shrink-0 rounded-full bg-slate-100 text-xl">{voice?'⌨️':'🎙️'}</button>
      {voice?<button type="button" onPointerDown={()=>onToast('语音功能即将上线')} className="min-w-0 flex-1 rounded-xl border bg-white px-3 font-medium">按住说话</button>:<input value={text} onChange={e=>onTextChange(e.target.value)} className="min-w-0 flex-1 rounded-xl border bg-slate-50 px-3" placeholder={placeholder}/>} 
      <button disabled={voice||!text.trim()} className="shrink-0 rounded-xl bg-emerald-600 px-3 font-medium text-white disabled:bg-slate-200 disabled:text-slate-400">发送</button>
      <button type="button" onClick={()=>onPlusChange(!plusOpen)} aria-label="更多功能" className={`h-11 w-11 shrink-0 rounded-full text-2xl ${plusOpen?'rotate-45 bg-slate-200':'bg-slate-100'}`}>＋</button>
    </div>
    <input ref={gallery} type="file" accept="image/*" className="hidden" onChange={pick}/><input ref={camera} type="file" accept="image/*" capture="environment" className="hidden" onChange={pick}/><input ref={file} type="file" className="hidden" onChange={pick}/><input ref={video} type="file" accept="video/*" className="hidden" onChange={pick}/>
  </form>;
}
