from sqlalchemy.orm import Session
from app.db.models import AIEmployee, User
from app.services.employee_service import ensure_default_employees
RANKS=["董事长","总经理","CEO","总裁","副总经理","项目主管","项目经理","产品经理","技术负责人"]
def get_highest_rank_employee(db:Session,user_id:int)->AIEmployee:
    rows=db.query(AIEmployee).filter(AIEmployee.user_id==user_id,AIEmployee.is_active.is_(True)).order_by(AIEmployee.created_at).all()
    if not rows:
        ensure_default_employees(db,db.get(User,user_id)); rows=db.query(AIEmployee).filter(AIEmployee.user_id==user_id,AIEmployee.is_active.is_(True)).order_by(AIEmployee.created_at).all()
    def score(x):
        for i,title in enumerate(RANKS):
            if title.lower() in x.position.lower(): return i
        return len(RANKS) if any(k in x.position for k in ["经理","主管","总监","负责人"]) else len(RANKS)+1
    return min(rows,key=score)
