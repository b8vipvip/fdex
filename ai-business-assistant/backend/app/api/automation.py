from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.models import IndustryKnowledge,Project,User,WorkGroup,WorkStageReport
from app.db.session import get_db
from app.services.company_simulation_service import generate_stage_report,start_auto_work_operation
from app.services.industry_knowledge_service import get_or_create_industry_basic_knowledge
router=APIRouter(tags=['automation'])
def owned(db,wid,user):
 p=db.get(Project,wid)
 if not p or p.user_id!=user.id:raise HTTPException(404,'工作不存在')
 return p
@router.post('/works/{wid}/auto-operation/start')
@router.post('/projects/{wid}/auto-operation/start')
def start(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 try:g=start_auto_work_operation(db,wid,user.id);return {'ok':True,'group_id':g.id,'status':'running'}
 except ValueError as e:raise HTTPException(400,str(e))
@router.post('/works/{wid}/auto-operation/pause')
@router.post('/projects/{wid}/auto-operation/pause')
def pause(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):p=owned(db,wid,user);p.auto_operation_status='paused';db.commit();return {'ok':True,'status':p.auto_operation_status}
@router.post('/works/{wid}/auto-operation/resume')
@router.post('/projects/{wid}/auto-operation/resume')
def resume(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):p=owned(db,wid,user);p.auto_operation_status='running';db.commit();return {'ok':True,'status':p.auto_operation_status}
@router.post('/works/{wid}/auto-operation/stage-report')
@router.post('/projects/{wid}/auto-operation/stage-report')
def report(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):p=owned(db,wid,user);g=db.get(WorkGroup,p.auto_operation_group_id);r=generate_stage_report(db,p,g);db.commit();return {c.name:getattr(r,c.name) for c in r.__table__.columns}
@router.get('/works/{wid}/auto-operation/status')
@router.get('/projects/{wid}/auto-operation/status')
def status(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
 p=owned(db,wid,user);reports=db.query(WorkStageReport).filter(WorkStageReport.work_id==wid).order_by(WorkStageReport.created_at.desc()).all();return {'status':p.auto_operation_status,'group_id':p.auto_operation_group_id,'stage':p.stage,'stage_summary':p.stage_summary,'reports':[{c.name:getattr(r,c.name) for c in r.__table__.columns} for r in reports]}
@router.get('/works/{wid}/groups')
def groups(wid:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):owned(db,wid,user);return [{c.name:getattr(g,c.name) for c in g.__table__.columns} for g in db.query(WorkGroup).filter(WorkGroup.work_id==wid,WorkGroup.status!='deleted')]
@router.get('/industry-knowledge/{industry}')
def knowledge(industry:str,db:Session=Depends(get_db),user:User=Depends(get_current_user)):k=db.query(IndustryKnowledge).filter(IndustryKnowledge.industry==industry).first();return {c.name:getattr(k,c.name) for c in k.__table__.columns} if k else None
@router.post('/industry-knowledge/{industry}/generate')
def generate(industry:str,db:Session=Depends(get_db),user:User=Depends(get_current_user)):k=get_or_create_industry_basic_knowledge(db,user.id,industry);db.commit();return {c.name:getattr(k,c.name) for c in k.__table__.columns}
