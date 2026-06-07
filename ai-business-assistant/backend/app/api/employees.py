import json, shutil, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import AIEmployee, DeletedMessageBatch, EmployeeMessage, EmployeeMessageAttachment, JobRole, Project, ProjectAsset, User
from app.db.session import get_db
from app.schemas.employee import ConversationResponse, EmployeeCreate, EmployeeMessageCreate, EmployeeMessageRead, EmployeeRead, EmployeeUpdate
from app.schemas.project import ProjectRead
from app.services.employee_service import confirm_create_project, ensure_default_employees, mock_reply
router=APIRouter(prefix='/employees',tags=['employees']); settings=get_settings()
def owned(db,id,user):
    x=db.get(AIEmployee,id)
    if not x or x.user_id!=user.id: raise HTTPException(404,'员工不存在')
    return x
def file_type(mime): return 'image' if mime.startswith('image/') else 'video' if mime.startswith('video/') else 'file'
@router.get('',response_model=list[EmployeeRead])
def list_employees(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    ensure_default_employees(db,user); return db.query(AIEmployee).filter(AIEmployee.user_id==user.id).order_by(AIEmployee.is_material_manager.desc(),AIEmployee.id).all()
@router.post('',response_model=EmployeeRead)
def create_employee(payload:EmployeeCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if payload.is_material_manager: raise HTTPException(400,'资料员由系统创建')
    data=payload.model_dump(); role=db.get(JobRole,data.get('job_role_id')) if data.get('job_role_id') else None
    if role: data.update(position=role.title,industry=role.industry,role_prompt=role.role_prompt_template)
    x=AIEmployee(user_id=user.id,**data);db.add(x);db.commit();db.refresh(x);return x
@router.get('/{employee_id}',response_model=EmployeeRead)
def get_employee(employee_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)): return owned(db,employee_id,user)
@router.put('/{employee_id}',response_model=EmployeeRead)
def update_employee(employee_id:int,payload:EmployeeUpdate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user); data=payload.model_dump(exclude_unset=True)
    if x.is_material_manager:
        for k in ('position','job_role_id','industry','role_prompt'): data.pop(k,None)
    elif 'job_role_id' in data and data['job_role_id']:
        role=db.get(JobRole,data['job_role_id'])
        if not role: raise HTTPException(400,'职位不存在')
        data.update(position=role.title,industry=role.industry,role_prompt=role.role_prompt_template)
    for k,v in data.items(): setattr(x,k,v)
    db.commit();db.refresh(x);return x
@router.post('/{employee_id}/avatar',response_model=EmployeeRead)
def avatar(employee_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user); suffix=Path(file.filename or '').suffix[:10]; name=f'employee-{user.id}-{uuid.uuid4().hex}{suffix}'; path=Path(settings.upload_dir)/name
    with path.open('wb') as out: shutil.copyfileobj(file.file,out)
    x.avatar_url=f'/uploads/{name}';db.commit();db.refresh(x);return x
@router.delete('/{employee_id}')
def delete_employee(employee_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user)
    if x.is_material_manager: raise HTTPException(400,'资料员不能删除')
    db.query(EmployeeMessage).filter(EmployeeMessage.employee_id==x.id).delete();db.delete(x);db.commit();return {'ok':True}
@router.get('/{employee_id}/messages',response_model=list[EmployeeMessageRead])
def messages(employee_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    owned(db,employee_id,user);return db.query(EmployeeMessage).filter(EmployeeMessage.employee_id==employee_id,EmployeeMessage.user_id==user.id,EmployeeMessage.deleted_at.is_(None)).order_by(EmployeeMessage.created_at).all()
@router.get('/{employee_id}/messages/search',response_model=list[EmployeeMessageRead])
def search_messages(employee_id:int,keyword:str='',date:str='',db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    owned(db,employee_id,user);q=db.query(EmployeeMessage).filter(EmployeeMessage.employee_id==employee_id,EmployeeMessage.user_id==user.id,EmployeeMessage.deleted_at.is_(None))
    if keyword:q=q.filter(EmployeeMessage.content.ilike(f'%{keyword}%'))
    if date:q=q.filter(EmployeeMessage.created_at>=datetime.fromisoformat(date),EmployeeMessage.created_at<datetime.fromisoformat(date)+timedelta(days=1))
    return q.order_by(EmployeeMessage.created_at.desc()).all()
@router.post('/{employee_id}/messages/clear')
def clear(employee_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    owned(db,employee_id,user);now=datetime.now(timezone.utc);b=DeletedMessageBatch(user_id=user.id,employee_id=employee_id,deleted_at=now,retention_days=user.deleted_retention_days,expires_at=now+timedelta(days=user.deleted_retention_days));db.add(b);db.flush();count=db.query(EmployeeMessage).filter(EmployeeMessage.employee_id==employee_id,EmployeeMessage.user_id==user.id,EmployeeMessage.deleted_at.is_(None)).update({'deleted_at':now,'deleted_batch_id':b.id});db.commit();return {'ok':True,'batch_id':b.id,'count':count}
@router.post('/{employee_id}/messages',response_model=ConversationResponse)
def create_message(employee_id:int,payload:EmployeeMessageCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user)
    if payload.project_id:
        p=db.get(Project,payload.project_id)
        if not p or p.user_id!=user.id: raise HTTPException(404,'项目不存在')
    sent=EmployeeMessage(user_id=user.id,employee_id=x.id,project_id=payload.project_id,role='user',content=payload.content,message_type='text'); reply_text,meta=mock_reply(x,payload.content);reply=EmployeeMessage(user_id=user.id,employee_id=x.id,project_id=payload.project_id,role='employee',content=reply_text,metadata_json=json.dumps(meta,ensure_ascii=False));db.add_all([sent,reply]);db.commit();db.refresh(sent);db.refresh(reply);return {'user_message':sent,'employee_message':reply}
@router.post('/{employee_id}/messages/attachments',response_model=EmployeeMessageRead)
def attachment(employee_id:int,file:UploadFile=File(...),project_id:int|None=Form(None),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user)
    if project_id:
        p=db.get(Project,project_id)
        if not p or p.user_id!=user.id: raise HTTPException(404,'项目不存在')
    original=file.filename or '附件'; name=f'message-{user.id}-{uuid.uuid4().hex}{Path(original).suffix[:10]}';path=Path(settings.upload_dir)/name
    with path.open('wb') as out: shutil.copyfileobj(file.file,out)
    size=path.stat().st_size;kind=file_type(file.content_type or '');meta={'filename':original,'url':f'/uploads/{name}','mime_type':file.content_type,'file_size':size};m=EmployeeMessage(user_id=user.id,employee_id=x.id,project_id=project_id,role='user',content=f'发送了{kind}：{original}',message_type=kind,metadata_json=json.dumps(meta,ensure_ascii=False));db.add(m);db.flush();db.add(EmployeeMessageAttachment(message_id=m.id,user_id=user.id,employee_id=x.id,project_id=project_id,filename=name,original_filename=original,file_path=str(path),file_type=kind,mime_type=file.content_type or '',file_size=size))
    if x.is_material_manager and project_id: db.add(ProjectAsset(project_id=project_id,filename=name,original_filename=original,file_path=str(path),file_type=kind,mime_type=file.content_type or '',file_size=size))
    db.commit();db.refresh(m);return m
@router.post('/{employee_id}/messages/{message_id}/confirm-create-project',response_model=ProjectRead)
def confirm(employee_id:int,message_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=owned(db,employee_id,user);m=db.get(EmployeeMessage,message_id)
    if not m or m.user_id!=user.id or m.employee_id!=x.id: raise HTTPException(404,'消息不存在')
    try:return confirm_create_project(db,x,m,user)
    except ValueError as e:raise HTTPException(400,str(e))
