import json
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.models import AIEmployee,Project,User,WorkGroup,WorkGroupMember,WorkGroupMessage
from app.db.session import get_db
from app.schemas.work_group import GroupMessageCreate,MemberAdd,WorkGroupCreate,WorkGroupUpdate
from app.services.company_simulation_service import add_message,generate_stage_report,task_for
router=APIRouter(prefix='/work-groups',tags=['work-groups'])
def owned(db,gid,user):
 g=db.get(WorkGroup,gid)
 if not g or g.user_id!=user.id or g.status=='deleted':raise HTTPException(404,'工作群不存在')
 return g
def group_dict(db,g):
 last=db.query(WorkGroupMessage).filter(WorkGroupMessage.group_id==g.id,WorkGroupMessage.deleted_at.is_(None)).order_by(WorkGroupMessage.created_at.desc()).first();p=db.get(Project,g.work_id) if g.work_id else None
 return {c.name:getattr(g,c.name) for c in g.__table__.columns}|{'work_name':p.title if p else None,'last_message':last.content if last else None,'last_message_at':last.created_at if last else None,'member_count':db.query(WorkGroupMember).filter(WorkGroupMember.group_id==g.id).count()}
@router.get('')
def listing(db:Session=Depends(get_db),user:User=Depends(get_current_user)):return [group_dict(db,g) for g in db.query(WorkGroup).filter(WorkGroup.user_id==user.id,WorkGroup.status!='deleted').order_by(WorkGroup.updated_at.desc()).all()]
@router.post('')
def create(payload:WorkGroupCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 if payload.work_id:
  p=db.get(Project,payload.work_id)
  if not p or p.user_id!=user.id:raise HTTPException(404,'工作不存在')
 g=WorkGroup(user_id=user.id,work_id=payload.work_id,name=payload.name,description=payload.description,group_type=payload.group_type,auto_mode_enabled=payload.auto_mode_enabled);db.add(g);db.flush()
 ids=payload.employee_ids or [e.id for e in db.query(AIEmployee).filter(AIEmployee.user_id==user.id,AIEmployee.is_material_manager.is_(True)).all()]
 for eid in set(ids):
  e=db.get(AIEmployee,eid)
  if e and e.user_id==user.id:db.add(WorkGroupMember(group_id=g.id,employee_id=e.id,role_in_group='material_manager' if e.is_material_manager else 'member'))
 add_message(db,g,user,'工作群已创建');db.commit();return group_dict(db,g)
@router.get('/{gid}')
def get(gid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):return group_dict(db,owned(db,gid,user))
@router.put('/{gid}')
def update(gid:int,payload:WorkGroupUpdate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 g=owned(db,gid,user)
 for k,v in payload.model_dump(exclude_unset=True).items():setattr(g,k,v)
 db.commit();return group_dict(db,g)
@router.delete('/{gid}')
def delete(gid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):g=owned(db,gid,user);g.status='deleted';db.commit();return {'ok':True}
@router.get('/{gid}/members')
def members(gid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 owned(db,gid,user);rows=db.query(WorkGroupMember).filter(WorkGroupMember.group_id==gid).all();out=[]
 for r in rows:
  e=db.get(AIEmployee,r.employee_id);out.append({c.name:getattr(r,c.name) for c in r.__table__.columns}|{'employee_name':e.name,'employee_position':e.position,'employee_avatar':e.avatar})
 return out
@router.post('/{gid}/members')
def add_members(gid:int,payload:MemberAdd,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 owned(db,gid,user);existing={x.employee_id for x in db.query(WorkGroupMember).filter(WorkGroupMember.group_id==gid)}
 for eid in payload.employee_ids:
  e=db.get(AIEmployee,eid)
  if e and e.user_id==user.id and eid not in existing:db.add(WorkGroupMember(group_id=gid,employee_id=eid,role_in_group='material_manager' if e.is_material_manager else 'member'))
 db.commit();return {'ok':True}
@router.delete('/{gid}/members/{eid}')
def remove(gid:int,eid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):owned(db,gid,user);db.query(WorkGroupMember).filter(WorkGroupMember.group_id==gid,WorkGroupMember.employee_id==eid).delete();db.commit();return {'ok':True}
@router.get('/{gid}/messages')
def messages(gid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 owned(db,gid,user);out=[]
 for m in db.query(WorkGroupMessage).filter(WorkGroupMessage.group_id==gid,WorkGroupMessage.deleted_at.is_(None)).order_by(WorkGroupMessage.created_at):
  e=db.get(AIEmployee,m.employee_id) if m.employee_id else None;out.append({c.name:getattr(m,c.name) for c in m.__table__.columns}|{'employee_name':e.name if e else None,'employee_position':e.position if e else None,'employee_avatar':e.avatar if e else None})
 return out
@router.post('/{gid}/messages')
def post_message(gid:int,payload:GroupMessageCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 g=owned(db,gid,user);add_message(db,g,user,payload.content,'user');text=payload.content
 members=[db.get(AIEmployee,x.employee_id) for x in db.query(WorkGroupMember).filter(WorkGroupMember.group_id==gid)]
 target=next((e for e in members if e and (f'@{e.name}' in text or e.position in text)),None)
 reply=None
 if '暂停' in text:g.auto_mode_enabled=False;reply='自动运营已暂停。你可以随时发送“继续”恢复。'
 elif '继续' in text:g.auto_mode_enabled=True;reply='自动运营已继续，团队将按当前分工推进。'
 elif '重新分工' in text:reply='收到，我将重新拆分任务，并按优先级安排给相关员工。'
 elif '生成阶段汇报' in text and g.work_id:
  report=generate_stage_report(db,db.get(Project,g.work_id),g,target or next((e for e in members if e),None));reply=f'阶段汇报已生成：{report.title}'
 elif target:reply=f'收到。我会按你的最高指挥要求，立即{task_for(target.position)}。'
 elif any(k in text for k in ['开始开会','分析这项工作','安排任务']):reply='会议流程已触发：先检查资料，再确认目标并安排任务。'
 if reply:add_message(db,g,user,reply,'employee' if target else 'system',target)
 db.commit();return {'ok':True}
@router.delete('/{gid}/messages')
def clear(gid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):owned(db,gid,user);db.query(WorkGroupMessage).filter(WorkGroupMessage.group_id==gid).delete();db.commit();return {'ok':True}
