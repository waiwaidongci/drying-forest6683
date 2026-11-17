from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey,Index,text
from ..config.database import Base
from ..config.settings import CONFIG
ACTIVE_APPLICATION_STATES=('freeze_queued','freeze_running','prediction_queued','prediction_running','awaiting_unblind','unblind_queued','unblind_running')
class BlindApplication(Base):
    __tablename__=CONFIG['blind_application_table']
    __table_args__=(
        # 同一盲测批同时只接受一个有效申请；失败/失效/已揭盲不占名额，旧研究仍可读
        Index('uq_blind_active_batch','batch_id',unique=True,sqlite_where=text("state IN ('freeze_queued','freeze_running','prediction_queued','prediction_running','awaiting_unblind','unblind_queued','unblind_running')"),postgresql_where=text("state IN ('freeze_queued','freeze_running','prediction_queued','prediction_running','awaiting_unblind','unblind_queued','unblind_running')")),
    )
    id=Column(Integer,primary_key=True)
    batch_id=Column(Integer,ForeignKey(CONFIG['blind_batch_table']+'.id'),nullable=False)
    # 申请即冻结：Run 行在创建时生成并持有训练快照，重试/改训练数据都不会换模型
    run_id=Column(Integer,ForeignKey(CONFIG['run_table']+'.id'),nullable=False)
    name=Column(String(160),nullable=False)
    applicant=Column(String(120),nullable=False)
    state=Column(String(30),default='freeze_queued',nullable=False)
    frozen_input_checksum=Column(String(64),nullable=False)
    model_checksum=Column(String(64),default='')
    predictions=Column(JSON,nullable=True)
    predictions_checksum=Column(String(64),default='')
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    attempts=Column(Integer,default=0,nullable=False)
    created_at=Column(String(40),nullable=False)
    frozen_at=Column(String(40),default='')
    predicted_at=Column(String(40),default='')
    revealed_at=Column(String(40),default='')
