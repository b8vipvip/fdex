from sqlalchemy.orm import Session
from app.db.models import IndustryKnowledge
class SearchAIProvider:
    def search_and_update_industry_knowledge(self,industry:str): raise NotImplementedError("SearchAI 接口占位，本轮不联网")
def get_or_create_industry_basic_knowledge(db:Session,user_id:int,industry:str)->IndustryKnowledge:
    row=db.query(IndustryKnowledge).filter(IndustryKnowledge.industry==industry).first()
    if row:return row
    content=f'''# {industry}公司基础运转流程资料\n\n> 系统 Mock 资料，仅供团队讨论参考，建议后续补充真实业务资料。\n\n## 1. 行业常见业务流程\n需求收集、方案设计、执行交付、质量检查与复盘优化。\n\n## 2. 常见部门与岗位\n管理、产品、技术、运营、市场、客服、财务与资料管理。\n\n## 3. 常见数据资料\n客户需求、业务流程、订单台账、运营数据和复盘记录。\n\n## 4. 常见管理指标\n交付周期、质量、成本、客户满意度与业务转化率。\n\n## 5. 常见自动化机会\n资料归档、任务提醒、数据汇总、风险预警与阶段汇报。\n\n## 6. AI 可参与的工作环节\n需求整理、资料检查、任务拆解、方案初稿和阶段总结。'''
    row=IndustryKnowledge(industry=industry,title=f"{industry}公司基础运转流程资料",content_markdown=content,source_type="mock");db.add(row);db.flush();return row
def search_and_update_industry_knowledge(industry:str): return SearchAIProvider().search_and_update_industry_knowledge(industry)
