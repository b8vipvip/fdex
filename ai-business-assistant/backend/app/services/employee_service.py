import json
import random
import re
from sqlalchemy.orm import Session
from app.db.models import AIEmployee, EmployeeMessage, Project, ProjectMessage, User

DEFAULT_ROLES = [
    ("管理层", "总经理 / CEO", "帮用户判断业务方向、资源投入、优先级和整体战略。", True),
    ("产品部", "产品经理", "把大白话需求转成 PRD、功能清单、用户流程和验收标准。", True),
    ("项目部", "项目经理", "拆解任务、安排进度、跟踪项目状态并提醒风险。", True),
    ("技术部", "技术负责人", "负责技术架构、技术选型、接口、数据库和部署方案。", True),
    ("技术部", "前端工程师", "负责页面结构、交互体验、移动端适配和前端实现建议。", False),
    ("技术部", "后端工程师", "负责 API、数据库、任务队列、文件处理、权限和安全。", False),
    ("设计部", "UI设计师", "负责界面风格、布局、移动端体验和视觉优化建议。", False),
    ("质量部", "测试工程师", "负责测试用例、验收标准、异常场景和质量保障。", False),
    ("运营部", "运营经理", "负责业务流程、用户增长、转化率、内容运营和落地执行。", True),
    ("市场部", "市场经理", "负责市场定位、获客策略、品牌传播和商业机会。", False),
    ("客户成功部", "客服主管", "负责客户问题、服务流程、反馈归纳和满意度。", False),
    ("数据部", "数据分析师", "负责数据指标、报表、异常分析和经营建议。", False),
    ("法务部", "法务顾问", "负责合同、知识产权、合规风险和法律提醒。", False),
    ("财务部", "财务顾问", "负责预算、成本、现金流和财务风险建议。", False),
    ("安全部", "隐私安全官", "负责隐私风险、敏感信息、合规提醒和权限建议。", False),
]
SURNAMES = "林陈周张李王赵刘杨黄吴徐孙胡朱高郭何罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程魏苏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
GIVEN = ["浩然", "思雨", "明远", "嘉宁", "子墨", "欣然", "宇航", "若溪", "启明", "安然", "景行", "知夏", "博文", "诗涵", "泽宇"]
AVATARS = ["👔", "🧭", "📋", "🧑‍💻", "💻", "⚙️", "🎨", "🧪", "📈", "📣", "💬", "📊", "⚖️", "💰", "🛡️"]


def ensure_default_employees(db: Session, user: User) -> list[AIEmployee]:
    existing = db.query(AIEmployee).filter(AIEmployee.user_id == user.id).all()
    if existing:
        if not any(x.is_material_manager for x in existing):
            db.add(AIEmployee(user_id=user.id, name="资料员", avatar="🗂️", department="资料管理部", position="资料员", role_prompt="负责接收、整理并关联项目资料。", is_system=True, is_material_manager=True, allow_upload_assets=True, allow_receive_project_context=True))
            db.commit()
        return db.query(AIEmployee).filter(AIEmployee.user_id == user.id).all()
    db.add(AIEmployee(user_id=user.id, name="资料员", avatar="🗂️", department="资料管理部", position="资料员", role_prompt="负责接收、整理并关联项目资料。", is_system=True, is_material_manager=True, allow_upload_assets=True, allow_receive_project_context=True))
    used = set()
    for index, (department, position, prompt, can_create) in enumerate(DEFAULT_ROLES):
        name = random.choice(SURNAMES) + random.choice(GIVEN)
        while name in used:
            name = random.choice(SURNAMES) + random.choice(GIVEN)
        used.add(name)
        employee = AIEmployee(user_id=user.id, name=name, avatar=AVATARS[index], department=department, position=position, role_prompt=prompt, can_create_project=can_create)
        db.add(employee)
        db.flush()
        db.add(EmployeeMessage(user_id=user.id, employee_id=employee.id, role="employee", content=f"你好，我是{name}，你的{position}。{prompt} 有需要随时告诉我。"))
    db.commit()
    return db.query(AIEmployee).filter(AIEmployee.user_id == user.id).all()


def mock_reply(employee: AIEmployee, content: str) -> tuple[str, dict]:
    metadata = {}
    create_match = re.search(r"(?:创建|新建)(?:一个)?项目[，,：:\s]*(?:做|名称是|叫)?[：:\s]*(.+)", content)
    if create_match and employee.can_create_project:
        title = create_match.group(1).strip("。！？!? ，,")[:80] or "新项目"
        metadata = {"action": "confirm_create_project", "status": "pending", "suggested_title": title, "source_content": content}
        return f"我可以帮你创建项目，项目名称建议为：{title}。请确认后我再创建，避免执行敏感操作。", metadata
    styles = {
        "产品经理": "我先把需求拆成目标用户、核心场景、功能范围和验收标准。建议先确认最重要的使用流程，再排 MVP。",
        "技术负责人": "从技术方案看，建议先明确数据来源、接口边界、数据库结构和部署方式，并优先验证高风险环节。",
        "项目经理": "我建议拆成需求确认、方案设计、实现、测试和上线五个阶段，并为每阶段明确负责人和截止时间。",
        "隐私安全官": "这项工作需要先识别敏感数据、最小化权限，并确认脱敏、保留期限和第三方 AI 使用范围。",
        "运营经理": "从业务落地看，建议先定义目标指标、小范围试运行，再根据用户反馈持续优化流程。",
    }
    prefix = styles.get(employee.position, f"我会从{employee.position}的职责出发，帮你梳理重点和下一步行动。")
    return f"收到。{prefix}\n\n针对你刚才提到的“{content[:60]}”，建议先补充期望结果、优先级和时间要求。", metadata


def confirm_create_project(db: Session, employee: AIEmployee, message: EmployeeMessage, user: User) -> Project:
    metadata = json.loads(message.metadata_json or "{}")
    if metadata.get("action") != "confirm_create_project" or metadata.get("status") != "pending":
        raise ValueError("该消息没有待确认的创建项目操作")
    project = Project(user_id=user.id, title=metadata.get("suggested_title", "新项目"), description=metadata.get("source_content", ""), requirement_score=25)
    db.add(project)
    db.flush()
    db.add(ProjectMessage(project_id=project.id, role="user", content=f"来源：与 {employee.name} · {employee.position} 的聊天\n{project.description}"))
    message.project_id = project.id
    metadata["status"] = "confirmed"
    metadata["project_id"] = project.id
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    db.add(EmployeeMessage(user_id=user.id, employee_id=employee.id, project_id=project.id, role="employee", content=f"已按你的确认创建项目「{project.title}」，并关联本次聊天记录。"))
    db.commit()
    db.refresh(project)
    return project
