import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.models import AIEmployee, EmployeeMessage, Project, User
from app.db.session import get_db
from app.schemas.employee import ConversationResponse, EmployeeCreate, EmployeeMessageCreate, EmployeeMessageRead, EmployeeRead, EmployeeUpdate
from app.schemas.project import ProjectRead
from app.services.employee_service import confirm_create_project, ensure_default_employees, mock_reply

router = APIRouter(prefix="/employees", tags=["employees"])


def get_owned_employee(db: Session, employee_id: int, user: User) -> AIEmployee:
    employee = db.get(AIEmployee, employee_id)
    if not employee or employee.user_id != user.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    return employee


@router.get("", response_model=list[EmployeeRead])
def list_employees(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_default_employees(db, user)
    return db.query(AIEmployee).filter(AIEmployee.user_id == user.id).order_by(AIEmployee.id).all()


@router.post("", response_model=EmployeeRead)
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    employee = AIEmployee(user_id=user.id, **payload.model_dump())
    db.add(employee); db.commit(); db.refresh(employee)
    return employee


@router.get("/{employee_id}", response_model=EmployeeRead)
def get_employee(employee_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_owned_employee(db, employee_id, user)


@router.put("/{employee_id}", response_model=EmployeeRead)
def update_employee(employee_id: int, payload: EmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    employee = get_owned_employee(db, employee_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items(): setattr(employee, field, value)
    db.commit(); db.refresh(employee)
    return employee


@router.delete("/{employee_id}")
def delete_employee(employee_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    employee = get_owned_employee(db, employee_id, user)
    db.query(EmployeeMessage).filter(EmployeeMessage.employee_id == employee.id).delete()
    db.delete(employee); db.commit()
    return {"ok": True}


@router.get("/{employee_id}/messages", response_model=list[EmployeeMessageRead])
def list_messages(employee_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_employee(db, employee_id, user)
    return db.query(EmployeeMessage).filter(EmployeeMessage.employee_id == employee_id, EmployeeMessage.user_id == user.id).order_by(EmployeeMessage.created_at).all()


@router.post("/{employee_id}/messages", response_model=ConversationResponse)
def create_message(employee_id: int, payload: EmployeeMessageCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    employee = get_owned_employee(db, employee_id, user)
    if payload.message_type != "text": raise HTTPException(status_code=400, detail="本轮仅支持文本消息")
    if payload.project_id:
        project = db.get(Project, payload.project_id)
        if not project or project.user_id != user.id: raise HTTPException(status_code=404, detail="项目不存在")
    sent = EmployeeMessage(user_id=user.id, employee_id=employee.id, project_id=payload.project_id, role="user", content=payload.content, message_type="text")
    reply_text, metadata = mock_reply(employee, payload.content)
    reply = EmployeeMessage(user_id=user.id, employee_id=employee.id, project_id=payload.project_id, role="employee", content=reply_text, metadata_json=json.dumps(metadata, ensure_ascii=False))
    db.add_all([sent, reply]); db.commit(); db.refresh(sent); db.refresh(reply)
    return {"user_message": sent, "employee_message": reply}


@router.post("/{employee_id}/messages/{message_id}/confirm-create-project", response_model=ProjectRead)
def confirm_project(employee_id: int, message_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    employee = get_owned_employee(db, employee_id, user)
    message = db.get(EmployeeMessage, message_id)
    if not message or message.user_id != user.id or message.employee_id != employee.id: raise HTTPException(status_code=404, detail="消息不存在")
    try: return confirm_create_project(db, employee, message, user)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
