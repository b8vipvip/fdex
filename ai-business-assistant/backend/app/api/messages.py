from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.api.employees import archive_messages, soft_clear
from app.db.models import AIEmployee, User
from app.db.session import get_db

router=APIRouter(prefix='/messages',tags=['messages'])
class ClearAllRequest(BaseModel): archive_to_work:bool=False
@router.post('/clear-all')
def clear_all(payload:ClearAllRequest,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    employees=db.query(AIEmployee).filter(AIEmployee.user_id==user.id).all()
    project=archive_messages(db,user,employees,f"全部员工聊天存档 {datetime.now().strftime('%Y-%m-%d')}") if payload.archive_to_work else None
    count=soft_clear(db,user,employees);db.commit();return {'ok':True,'count':count,'project_id':project.id if project else None}
