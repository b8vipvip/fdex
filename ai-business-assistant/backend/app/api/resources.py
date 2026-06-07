from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil, uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import AIEmployee, DeletedMessageBatch, EmployeeMessage, JobRole, Project, ProjectAsset, User
from app.db.session import get_db
from app.services.job_role_service import seed_job_roles

router = APIRouter(tags=["resources"]); settings=get_settings()

def job_dict(x): return {c.name:getattr(x,c.name) for c in x.__table__.columns}
@router.get('/job-roles')
def job_roles(keyword:str='',industry:str='',category:str='',is_common:bool|None=None,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    seed_job_roles(db); q=db.query(JobRole)
    if keyword: q=q.filter(or_(JobRole.title.ilike(f'%{keyword}%'),JobRole.aliases.ilike(f'%{keyword}%'),JobRole.description.ilike(f'%{keyword}%')))
    if industry: q=q.filter(JobRole.industry==industry)
    if category: q=q.filter(JobRole.category==category)
    if is_common is not None: q=q.filter(JobRole.is_common==is_common)
    return [job_dict(x) for x in q.order_by(JobRole.is_common.desc(),JobRole.sort_order).all()]
@router.get('/job-roles/industries')
def industries(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    seed_job_roles(db); return [x[0] for x in db.query(JobRole.industry).distinct().order_by(JobRole.industry).all()]
@router.get('/deleted-messages')
def deleted(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    rows=db.query(DeletedMessageBatch).filter(DeletedMessageBatch.user_id==user.id,DeletedMessageBatch.restored_at.is_(None)).order_by(DeletedMessageBatch.deleted_at.desc()).all()
    return [{**job_dict(x),'employee_name':db.get(AIEmployee,x.employee_id).name if db.get(AIEmployee,x.employee_id) else '未知员工','message_count':db.query(EmployeeMessage).filter(EmployeeMessage.deleted_batch_id==x.id).count()} for x in rows]
@router.post('/deleted-messages/{batch_id}/restore')
def restore(batch_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=db.get(DeletedMessageBatch,batch_id)
    if not x or x.user_id!=user.id: raise HTTPException(404,'记录不存在')
    db.query(EmployeeMessage).filter(EmployeeMessage.deleted_batch_id==x.id).update({'deleted_at':None,'deleted_batch_id':None}); x.restored_at=datetime.now(timezone.utc); db.commit(); return {'ok':True}
@router.delete('/deleted-messages/{batch_id}')
def purge(batch_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=db.get(DeletedMessageBatch,batch_id)
    if not x or x.user_id!=user.id: raise HTTPException(404,'记录不存在')
    db.query(EmployeeMessage).filter(EmployeeMessage.deleted_batch_id==x.id).delete(); db.delete(x); db.commit(); return {'ok':True}
@router.get('/search')
def search(q:str=Query(min_length=1),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    like=f'%{q}%'; out=[]
    for x in db.query(AIEmployee).filter(AIEmployee.user_id==user.id,or_(AIEmployee.name.ilike(like),AIEmployee.position.ilike(like))).limit(20): out.append({'source':'来自员工','title':x.name,'content':x.position,'url':f'/messages/{x.id}'})
    for x in db.query(EmployeeMessage).filter(EmployeeMessage.user_id==user.id,EmployeeMessage.deleted_at.is_(None),EmployeeMessage.content.ilike(like)).limit(20): out.append({'source':'来自聊天','title':db.get(AIEmployee,x.employee_id).name,'content':x.content,'url':f'/messages/{x.employee_id}'})
    for x in db.query(Project).filter(Project.user_id==user.id,or_(Project.title.ilike(like),Project.description.ilike(like))).limit(20): out.append({'source':'来自项目','title':x.title,'content':x.description,'url':f'/projects/{x.id}'})
    for x in db.query(ProjectAsset).join(Project).filter(Project.user_id==user.id,ProjectAsset.original_filename.ilike(like)).limit(20): out.append({'source':'来自资料','title':x.original_filename,'content':'项目资料','url':f'/projects/{x.project_id}'})
    return out
def profile_dict(user): return {k:getattr(user,k) for k in ('id','email','name','avatar','company_name','is_verified_company','realname_verified','deleted_retention_days','professional_level','created_at')}
@router.get('/account/profile')
def profile(user:User=Depends(get_current_user)): return profile_dict(user)
@router.put('/account/profile')
def update_profile(payload:dict,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    for k in ('name','company_name','deleted_retention_days'):
        if k in payload: setattr(user,k,payload[k])
    db.commit(); db.refresh(user); return profile_dict(user)
@router.post('/account/name-risk-check')
def risk(payload:dict,user:User=Depends(get_current_user)):
    brands=['腾讯','阿里巴巴','字节跳动','百度','华为','苹果','微软','谷歌','OpenAI','Meta','京东','美团','小米','拼多多']; found=[x for x in brands if x.lower() in str(payload.get('company_name','')).lower()]; return {'risk':bool(found),'matched':found,'message':'仅做知名品牌词提示，请自行核实名称是否可用。'}

@router.get('/projects/{project_id}/material-chat')
def material_chat(project_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    project=db.get(Project,project_id)
    if not project or project.user_id!=user.id: raise HTTPException(404,'项目不存在')
    employee=db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.is_material_manager.is_(True)).first()
    if not employee: raise HTTPException(404,'资料员不存在，请先打开员工列表初始化')
    return {'project_id':project_id,'employee_id':employee.id,'chat_url':f'/messages/{employee.id}?project_id={project_id}'}
