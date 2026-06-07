from datetime import datetime
from pydantic import BaseModel, Field

class WorkGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    work_id: int | None = None
    employee_ids: list[int] = []
    group_type: str = "manual"
    auto_mode_enabled: bool = False
class WorkGroupUpdate(BaseModel):
    name: str | None = None; description: str | None = None; status: str | None = None; auto_mode_enabled: bool | None = None
class WorkGroupRead(BaseModel):
    id:int; user_id:int; work_id:int|None; name:str; description:str; group_type:str; status:str; avatar_url:str; created_by:str; auto_mode_enabled:bool; created_at:datetime; updated_at:datetime
    work_name:str|None=None; last_message:str|None=None; last_message_at:datetime|None=None; member_count:int=0
    model_config={"from_attributes":True}
class MemberAdd(BaseModel): employee_ids:list[int]
class MemberRead(BaseModel):
    id:int; group_id:int; employee_id:int; role_in_group:str; joined_at:datetime; is_host:bool; employee_name:str=""; employee_position:str=""; employee_avatar:str="🤖"
    model_config={"from_attributes":True}
class GroupMessageCreate(BaseModel): content:str=Field(min_length=1); message_type:str="text"
class GroupMessageRead(BaseModel):
    id:int; group_id:int; user_id:int; employee_id:int|None; role:str; content:str; message_type:str; metadata_json:str; created_at:datetime; employee_name:str|None=None; employee_position:str|None=None; employee_avatar:str|None=None
    model_config={"from_attributes":True}
