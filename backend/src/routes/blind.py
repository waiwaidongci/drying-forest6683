from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.blind import BlindBatchInput,BlindBatchReplace,BlindApplicationInput
from ..services.auth import current_user,writer
from ..controllers.blind import BlindController as C
router=APIRouter()
# 盲测批：封存、占用、已揭盲三态；普通查看不返回实测时长标签
@router.get('/blind/batches')
def batches(db=Depends(db_session),u=Depends(current_user)):return C.list_batches(db)
@router.get('/blind/batches/{id}')
def batch(id:int,db=Depends(db_session),u=Depends(current_user)):return C.batch(db,id)
@router.post('/blind/batches')
def seal_batch(data:BlindBatchInput,db=Depends(db_session),u=Depends(writer)):return C.create_batch(db,u,data)
@router.put('/blind/batches/{id}/inputs')
def change_inputs(id:int,data:BlindBatchReplace,db=Depends(db_session),u=Depends(writer)):return C.replace_inputs(db,u,id,data)
# 盲测申请：冻结模型→写出预测→一次揭盲；同批并发只接受一个
@router.get('/blind/applications')
def applications(db=Depends(db_session),u=Depends(current_user)):return C.list_applications(db)
@router.get('/blind/applications/{id}')
def application(id:int,db=Depends(db_session),u=Depends(current_user)):return C.application(db,id)
@router.post('/blind/applications')
def apply(data:BlindApplicationInput,db=Depends(db_session),u=Depends(writer)):return C.create_application(db,u,data)
@router.post('/blind/applications/{id}/retry')
def retry(id:int,db=Depends(db_session),u=Depends(writer)):return C.retry(db,u,id)
@router.post('/blind/applications/{id}/unblind')
def unblind(id:int,db=Depends(db_session),u=Depends(writer)):return C.unblind(db,u,id)
@router.get('/blind/applications/{id}/export')
def export(id:int,db=Depends(db_session),u=Depends(current_user)):return C.export(db,id)
