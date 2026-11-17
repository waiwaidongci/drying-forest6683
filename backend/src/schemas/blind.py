from pydantic import BaseModel,Field
class BlindRecordInput(BaseModel):
    reference:str=Field(min_length=1,max_length=120)
    values:dict
class BlindBatchInput(BaseModel):
    name:str=Field(min_length=2,max_length=160)
    inputs:list[BlindRecordInput]
    # 实测时长标签，仅在请求内存活；后端封存为受限工件，不入库、不回显
    labels:list[float]
class BlindBatchReplace(BaseModel):
    inputs:list[BlindRecordInput]
class BlindApplicationInput(BaseModel):
    batch_id:int
    source_id:int
    name:str=Field(min_length=2,max_length=160)
    parameters:dict
