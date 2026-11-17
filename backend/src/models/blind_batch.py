from sqlalchemy import Column,Integer,String,JSON
from ..config.database import Base
from ..config.settings import CONFIG
class BlindBatch(Base):
    __tablename__=CONFIG['blind_batch_table']
    id=Column(Integer,primary_key=True)
    name=Column(String(160),nullable=False)
    # sealed 封存 / occupied 占用 / revealed 已揭盲
    state=Column(String(20),default='sealed',nullable=False)
    # 盲测输入只保存四个特征列；实测时长标签永不落库，单独封存为工件文件
    inputs=Column(JSON,nullable=False)
    references=Column(JSON,nullable=False)
    input_count=Column(Integer,nullable=False)
    input_checksum=Column(String(64),nullable=False)
    labels_checksum=Column(String(64),nullable=False)
    labels_path=Column(String(300),nullable=False)
    result=Column(JSON,nullable=True)
    created_at=Column(String(40),nullable=False)
    sealed_at=Column(String(40),nullable=False)
    revealed_at=Column(String(40),default='')
