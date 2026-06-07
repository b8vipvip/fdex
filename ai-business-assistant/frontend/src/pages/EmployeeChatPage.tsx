import {useEffect,useRef,useState} from 'react';
import {Link,useNavigate,useParams,useSearchParams} from 'react-router-dom';
import Layout from '../components/Layout';
import EmployeeAvatar from '../components/EmployeeAvatar';
import ChatInputBar from '../components/ChatInputBar';
import {API_BASE_URL} from '../api/client';
import {clearMessages,confirmCreateProject,getEmployee,getEmployeeMessages,sendEmployeeMessage,uploadMessageAttachment} from '../api/employees';
import type {Employee,EmployeeMessage} from '../types';

const absoluteUrl=(url:string)=>url.startsWith('http')?url:API_BASE_URL.replace(/\/api$/,'')+url;
export default function EmployeeChatPage(){
  const {employeeId}=useParams();const [sp]=useSearchParams();const id=Number(employeeId),projectId=Number(sp.get('project_id'))||undefined;
  const [employee,setEmployee]=useState<Employee>();const [messages,setMessages]=useState<EmployeeMessage[]>([]);const [text,setText]=useState('');const [menu,setMenu]=useState(false);const [plus,setPlus]=useState(false);const [toast,setToast]=useState('');
  const end=useRef<HTMLDivElement>(null);const nav=useNavigate();
  const load=()=>Promise.all([getEmployee(id),getEmployeeMessages(id)]).then(([e,m])=>{setEmployee(e);setMessages(m)});
  useEffect(()=>{void load()},[id]);useEffect(()=>end.current?.scrollIntoView({block:'end'}),[messages,plus]);
  function notify(message:string){setToast(message);window.setTimeout(()=>setToast(''),1800)}
  async function send(){if(!text.trim())return;const r=await sendEmployeeMessage(id,text.trim(),projectId);setMessages(x=>[...x,r.user_message,r.employee_message]);setText('')}
  async function attach(file:File){const uploaded=await uploadMessageAttachment(id,file,projectId);setMessages(x=>[...x,uploaded]);setPlus(false)}
  return <Layout title={employee?.name||'聊天'} actions={<button onClick={()=>setMenu(!menu)} className="w-11 text-xl" aria-label="聊天菜单">•••</button>}>
    {menu&&<div className="fixed right-3 top-14 z-50 w-48 overflow-hidden rounded-xl bg-white shadow-xl"><Link className="block min-h-11 p-3" to={`/employees/${id}`}>员工资料</Link><Link className="block min-h-11 p-3" to={`/messages/${id}/history`}>聊天记录</Link><button className="w-full p-3 text-left text-red-600" onClick={async()=>{if(confirm('清空后可在最近删除恢复，确认清空？')){await clearMessages(id);setMessages([]);setMenu(false)}}}>清空聊天记录</button></div>}
    <div onClick={()=>{setPlus(false);setMenu(false)}} className={`space-y-4 ${plus?'pb-[330px]':'pb-24'}`}>{messages.map(m=>{let meta:any={};try{meta=JSON.parse(m.metadata_json)}catch{};return <div key={m.id} className={`flex gap-2 ${m.role==='user'?'justify-end':'justify-start'}`}>{m.role!=='user'&&employee&&<EmployeeAvatar employee={employee}/>}<div className="max-w-[78%]"><p className={`mb-1 text-xs text-slate-500 ${m.role==='user'?'text-right':''}`}>{m.role==='user'?'我':employee?.name}</p><div className={`rounded-2xl px-4 py-3 text-[15px] leading-6 shadow-sm ${m.role==='user'?'rounded-tr-sm bg-emerald-500 text-white':'rounded-tl-sm bg-white'}`}>
      {m.message_type==='image'&&meta.url?<img src={absoluteUrl(meta.url)} alt={meta.filename||'聊天图片'} className="mb-2 max-h-64 w-full rounded-xl object-cover"/>:null}
      {m.message_type!=='image'&&meta.filename?<div className="mb-2 rounded-xl bg-black/5 p-2">{m.message_type==='video'?'🎬':'📎'} {meta.filename}</div>:null}
      {(!meta.filename||m.message_type==='text')&&<p className="whitespace-pre-wrap break-words">{m.content}</p>}
      {meta.action==='confirm_create_project'&&meta.status==='pending'&&<button onClick={async e=>{e.stopPropagation();nav(`/projects/${(await confirmCreateProject(id,m.id)).id}`)}} className="mt-3 w-full rounded-xl bg-emerald-700 px-3 text-white">确认创建项目</button>}
    </div></div></div>})}<div ref={end}/></div>
    <ChatInputBar text={text} onTextChange={setText} onSend={()=>void send()} onAttach={file=>void attach(file)} plusOpen={plus} onPlusChange={setPlus} placeholder={projectId?'发送并关联当前项目…':'给员工安排任务...'} onToast={notify}/>
    {toast&&<div className="fixed left-1/2 top-1/2 z-[70] -translate-x-1/2 rounded-xl bg-black/75 px-4 py-3 text-sm text-white shadow-lg">{toast}</div>}
  </Layout>;
}
