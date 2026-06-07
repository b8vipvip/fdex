import json, random, shutil, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
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
    ensure_default_employees(db,user); return db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.is_active.is_(True)).order_by(AIEmployee.is_material_manager.desc(),AIEmployee.id).all()
@router.post('',response_model=EmployeeRead)
def create_employee(payload:EmployeeCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if payload.is_material_manager: raise HTTPException(400,'资料员由系统创建')
    data=payload.model_dump(); role=db.get(JobRole,data.get('job_role_id')) if data.get('job_role_id') else None
    if role: data.update(position=role.title,industry=role.industry,role_prompt=data.get('role_prompt') or role.role_prompt_template)
    x=AIEmployee(user_id=user.id,**data);db.add(x);db.commit();db.refresh(x);return x
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵季贾路娄江童颜郭梅林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包左石崔吉龚程嵇邢滑裴陆荣翁荀羊甄家封芮储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
GIVEN = "子涵宇轩欣怡梓豪雨桐思远若曦嘉诚语嫣浩然诗涵俊杰梦瑶一诺可欣明哲佳宁博文晓彤泽宇婉清天佑心怡睿哲安然"
def random_name(): return random.choice(SURNAMES) + random.choice([GIVEN[i:i+2] for i in range(0,len(GIVEN)-1,2)])
def archive_messages(db, user, employees, title):
    rows=db.query(EmployeeMessage).filter(EmployeeMessage.user_id==user.id,EmployeeMessage.employee_id.in_([e.id for e in employees]),EmployeeMessage.deleted_at.is_(None)).order_by(EmployeeMessage.created_at).all() if employees else []
    content='\n\n'.join(f"- {m.created_at}: {m.role}：{m.content}" for m in rows) or '暂无聊天消息。'
    project=Project(user_id=user.id,title=title,description=f"员工聊天存档\n\n{content}",status='archived',project_type='chat_archive')
    db.add(project); db.flush(); return project

def soft_clear(db,user,employees):
    now=datetime.now(timezone.utc); total=0
    for e in employees:
        b=DeletedMessageBatch(user_id=user.id,employee_id=e.id,deleted_at=now,retention_days=user.deleted_retention_days,expires_at=now+timedelta(days=user.deleted_retention_days));db.add(b);db.flush()
        total += db.query(EmployeeMessage).filter(EmployeeMessage.employee_id==e.id,EmployeeMessage.user_id==user.id,EmployeeMessage.deleted_at.is_(None)).update({'deleted_at':now,'deleted_batch_id':b.id})
    return total

class IndustryBulkCreate(BaseModel): industry:str; replace_existing:bool=True
class BulkResign(BaseModel): employee_ids:list[int]=[]; archive_mode:str='none'

@router.post('/bulk-create-by-industry',response_model=list[EmployeeRead])
def bulk_create_by_industry(payload:IndustryBulkCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    roles=db.query(JobRole).filter(JobRole.industry==payload.industry).order_by(JobRole.sort_order).all()
    if not roles: raise HTTPException(404,'行业职位不存在')
    if payload.replace_existing:
        existing=db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.is_material_manager.is_(False),AIEmployee.is_active.is_(True)).all();soft_clear(db,user,existing);now=datetime.now(timezone.utc)
        for employee in existing: employee.is_active=False;employee.resigned_at=now
    created=[]
    for role in roles:
        x=AIEmployee(user_id=user.id,name=random_name(),avatar=random.choice(['🤖','🧑‍💼','👩‍💻','🧑‍🚀','👩‍🔬']),department=role.industry,position=role.title,job_role_id=role.id,role_prompt=role.role_prompt_template,industry=role.industry);db.add(x);created.append(x)
    db.commit()
    for x in created: db.refresh(x)
    return created

@router.post('/create-general',response_model=EmployeeRead)
def create_general(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=AIEmployee(user_id=user.id,name=random_name(),avatar=random.choice(['🤖','🧑‍💼','👩‍💻']),department='未分配',position='普通员工',industry='通用',role_prompt='你是用户的普通 AI 助手，负责接收任务、整理信息、提醒用户补充上下文，并在需要时把任务转交给更合适的专业员工。')
    db.add(x);db.commit();db.refresh(x);return x

@router.post('/bulk-resign')
def bulk_resign(payload:BulkResign,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    employees=db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.id.in_(payload.employee_ids),AIEmployee.is_material_manager.is_(False),AIEmployee.is_active.is_(True)).all()
    project=None
    if payload.archive_mode!='none' and employees: project=archive_messages(db,user,employees,'员工聊天存档')
    soft_clear(db,user,employees)
    now=datetime.now(timezone.utc)
    for e in employees: e.is_active=False;e.resigned_at=now
    db.commit();return {'ok':True,'count':len(employees),'project_id':project.id if project else None}

@router.post('/resign-all')
def resign_all(payload:BulkResign,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    employees=db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.is_material_manager.is_(False),AIEmployee.is_active.is_(True)).all();payload.employee_ids=[e.id for e in employees];return bulk_resign(payload,db,user)

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
        data.update(position=role.title,industry=role.industry,role_prompt=data.get('role_prompt') or role.role_prompt_template)
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
    if x.is_material_manager: raise HTTPException(400,'资料员不能离职')
    soft_clear(db,user,[x]);x.is_active=False;x.resigned_at=datetime.now(timezone.utc);db.commit();return {'ok':True}
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
