import {useLocation,useNavigate} from 'react-router-dom';

function fallbackPath(pathname:string){
  if(pathname.startsWith('/messages/')) return '/messages';
  if(pathname.startsWith('/employees/')) return '/messages';
  if(pathname.startsWith('/projects/')) return '/projects';
  if(pathname.startsWith('/reports/')) return '/projects';
  if(pathname.startsWith('/me/')) return '/me';
  return '/messages';
}

export default function BackButton(){
  const nav=useNavigate();
  const {pathname}=useLocation();
  function goBack(){
    const index=Number(window.history.state?.idx??0);
    if(index>0) nav(-1);
    else nav(fallbackPath(pathname),{replace:true});
  }
  return <button type="button" onClick={goBack} aria-label="返回" className="flex min-h-11 shrink-0 items-center gap-0.5 rounded-lg px-1.5 text-base font-medium text-emerald-700 hover:bg-emerald-50"><span aria-hidden className="text-2xl leading-none">‹</span><span className="hidden sm:inline">返回</span></button>;
}
