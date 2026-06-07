import json
from sqlalchemy.orm import Session
from app.db.models import AIEmployee, IndustryKnowledge, Project, ProjectAsset, ProjectMessage, User, WorkGroup, WorkGroupMember, WorkGroupMessage, WorkStageReport
from app.services.employee_rank_service import get_highest_rank_employee
from app.services.industry_knowledge_service import get_or_create_industry_basic_knowledge
class CompanySimulationAIProvider: pass
class WorkPlanningAIProvider: pass
TASKS={"CEO":"定方向、目标、优先级和资源安排","总经理":"定方向、目标、优先级和资源安排","项目经理":"拆解任务、排期并跟踪风险","产品经理":"整理需求、用户流程和功能范围","技术负责人":"输出技术可行性、架构和接口方案","UI设计师":"梳理页面结构与移动端体验","运营经理":"梳理业务流程、增长和转化路径","数据分析师":"建立指标体系与分析口径","法务":"检查合规风险、数据隐私和权限边界","资料员":"收集整理资料并标注缺失信息"}
def task_for(position:str)->str:
    return next((v for k,v in TASKS.items() if k.lower() in position.lower()),f"从{position}职责出发提出建议并执行分工")
def add_message(db,group,user,content,role="system",employee=None,metadata=None):
    row=WorkGroupMessage(group_id=group.id,user_id=user.id,employee_id=employee.id if employee else None,role=role,content=content,message_type="system" if role=="system" else "text",metadata_json=json.dumps(metadata or {},ensure_ascii=False));db.add(row);return row
def generate_stage_report(db:Session,project:Project,group:WorkGroup,owner:AIEmployee|None=None):
    report=WorkStageReport(work_id=project.id,group_id=group.id,stage=project.stage or "阶段 3：任务分工完成",title=f"{project.title}阶段汇报",summary_markdown="## 已完成事项\n- 完成需求理解、资料检查与初步任务分工\n\n## 待补充资料\n- 真实业务数据与优先级约束\n\n## 下一步建议\n- 按分工执行并在群内持续汇报",owner_employee_id=owner.id if owner else None);db.add(report);project.stage_summary=report.summary_markdown;project.auto_operation_status="reported";return report
def start_auto_work_operation(db:Session,work_id:int,user_id:int):
    project=db.get(Project,work_id);user=db.get(User,user_id)
    if not project or project.user_id!=user_id: raise ValueError("工作不存在")
    if not user.company_industry: raise ValueError("公司自动化运行模式需要先选择公司行业。")
    host=get_highest_rank_employee(db,user_id); employees=db.query(AIEmployee).filter(AIEmployee.user_id==user_id,AIEmployee.is_active.is_(True)).order_by(AIEmployee.created_at).all()
    group=WorkGroup(user_id=user_id,work_id=project.id,name=f"{project.title} · 工作会议群",description="公司自动化运行模式创建的工作协作群",group_type="work_auto",created_by="system",auto_mode_enabled=True);db.add(group);db.flush()
    for e in employees: db.add(WorkGroupMember(group_id=group.id,employee_id=e.id,role_in_group="material_manager" if e.is_material_manager else ("host" if e.id==host.id else "member"),is_host=e.id==host.id))
    add_message(db,group,user,"会议已创建");add_message(db,group,user,f"公司自动化运行模式已启动。本群用于推进工作：{project.title}。");add_message(db,group,user,"我将作为本次工作的负责人，先组织大家进行需求理解、资料检查和任务分工。","employee",host);add_message(db,group,user,"资料员正在检查资料")
    insufficient=db.query(ProjectAsset).filter(ProjectAsset.project_id==project.id).count()==0 or len(project.description)<50
    material=next((e for e in employees if e.is_material_manager or "资料员" in e.position),None)
    if insufficient:
        add_message(db,group,user,"当前工作资料不足，我将根据公司行业整理一份基础流程资料，供团队参考。","employee",material);add_message(db,group,user,"资料不足，正在生成行业基础资料")
        knowledge=get_or_create_industry_basic_knowledge(db,user_id,user.company_industry);db.add(ProjectMessage(project_id=project.id,role="system",content=f"[system_generated=true]\n{knowledge.content_markdown}"));add_message(db,group,user,"阶段 2：资料整理完成")
    add_message(db,group,user,f"{host.position}正在安排任务");add_message(db,group,user,"员工正在提交初步汇报")
    for e in employees: add_message(db,group,user,f"收到，我负责{task_for(e.position)}。我会先输出初步结果，并及时汇报风险和待确认事项。","employee",e)
    add_message(db,group,user,"阶段 1：需求理解完成");add_message(db,group,user,"阶段 3：任务分工完成")
    project.auto_operation_group_id=group.id;project.auto_operation_status="running";project.stage="阶段 3：任务分工完成";project.industry=user.company_industry;project.status="running";generate_stage_report(db,project,group,host);db.commit();db.refresh(group);return group
