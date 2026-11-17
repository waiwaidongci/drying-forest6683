import json,hashlib
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from redis import Redis
from ..config.database import Session
from ..config.settings import ARTIFACT_DIR,REDIS_URL
from ..models.blind_batch import BlindBatch
from ..models.blind_application import BlindApplication,ACTIVE_APPLICATION_STATES
from ..models.run import Run
from ..models.record import Record
from ..utils.serialization import row,now
from .sources import source_or_404,list_records
from .domain_validation import validate_parameters,validate_blind_input,validate_blind_labels
from .algorithm import fit_blind_model,predict_blind,unblind_metrics
from .audit import log
BLIND_QUEUE='blind:queue'
queue=Redis.from_url(REDIS_URL)
BLIND_DIR=ARTIFACT_DIR/'blind'
def _blind_dir():
    # 按需创建，避免模块导入阶段因工件目录暂不可用而拖垮整个应用
    BLIND_DIR.mkdir(parents=True,exist_ok=True)
    return BLIND_DIR
def _checksum(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def _seal_labels(batch_id,labels):
    # 标签仅写入受限工件文件，数据库不保存；普通查看与模型计算进程都读不到
    path=_blind_dir()/f'labels-{batch_id}.json'
    path.write_text(json.dumps(labels,ensure_ascii=False),encoding='utf-8')
    path.chmod(0o600)
    return str(path)
def _load_labels(path):
    p=Path(path)
    if not p.exists():raise RuntimeError('封存标签工件缺失，无法揭盲')
    return json.loads(p.read_text(encoding='utf-8'))
def _public_batch(b):
    d=row(b)
    d.pop('labels_path',None)
    # 列表/详情均不回传可能被误用的封存路径；标签从未入库
    return d
def _assert_reference_isolation(db,blind_refs,source_id):
    overlap=db.scalars(select(Record.reference).where(Record.source_id==source_id,Record.reference.in_(blind_refs))).all()
    if overlap:raise HTTPException(422,'训练来源与盲测批存在相同试件编号，破坏按试件隔离：'+','.join(overlap[:5]))
def _assert_seal_isolation(db,blind_refs):
    # 封存即校验：盲测试件编号不得与任何已录入训练样本重合
    overlap=db.scalars(select(Record.reference).where(Record.reference.in_(blind_refs))).all()
    if overlap:raise HTTPException(422,'盲测试件编号已存在于训练样本，必须按试件编号隔离：'+','.join(overlap[:5]))
def create_batch(db,user,data):
    if len(data.inputs)<10:raise HTTPException(422,'盲测批至少封存10条样本')
    refs=[];inputs=[];seen=set()
    for item in data.inputs:
        if item.reference in seen:raise HTTPException(422,'批内试件编号重复：'+item.reference)
        seen.add(item.reference)
        try:values=validate_blind_input(item.values)
        except ValueError as e:raise HTTPException(422,str(e))
        inputs.append({'reference':item.reference,'values':values})
        refs.append(item.reference)
    try:labels=validate_blind_labels(data.labels,len(inputs))
    except ValueError as e:raise HTTPException(422,str(e))
    _assert_seal_isolation(db,refs)
    b=BlindBatch(name=data.name,state='sealed',inputs=inputs,references=refs,input_count=len(inputs),input_checksum='',labels_checksum=_checksum(labels),labels_path='',result=None,created_at=now(),sealed_at=now())
    db.add(b);db.flush()
    b.input_checksum=_checksum(inputs)
    b.labels_path=_seal_labels(b.id,labels)
    log(db,user,'blind_batch.sealed',b.id,f'{b.name} · {b.input_count}条 · 标签已封存')
    db.commit()
    return _public_batch(b)
def list_batches(db):
    return [_public_batch(b) for b in db.scalars(select(BlindBatch).order_by(BlindBatch.id.desc()))]
def get_batch(db,id):
    b=db.get(BlindBatch,id)
    if not b:raise HTTPException(404,'未找到盲测批')
    return b
def batch_detail(db,id):
    return _public_batch(get_batch(db,id))
def replace_inputs(db,user,id,data):
    b=get_batch(db,id)
    if b.state=='revealed':raise HTTPException(409,'已揭盲批的输入是误差证据，不允许改动')
    if len(data.inputs)!=b.input_count:raise HTTPException(422,'改动后条数必须与封存条数一致：'+str(b.input_count))
    refs=[];inputs=[];seen=set()
    for item in data.inputs:
        if item.reference in seen:raise HTTPException(422,'批内试件编号重复：'+item.reference)
        seen.add(item.reference)
        try:values=validate_blind_input(item.values)
        except ValueError as e:raise HTTPException(422,str(e))
        inputs.append({'reference':item.reference,'values':values})
        refs.append(item.reference)
    if sorted(refs)!=sorted(b.references):raise HTTPException(422,'试件编号集合必须保持不变，仅允许修改特征取值')
    b.inputs=inputs;b.input_checksum=_checksum(inputs)
    # 改动盲测输入使所有未揭盲申请失效；冻结模型不被重算，重试被拒绝
    apps=db.scalars(select(BlindApplication).where(BlindApplication.batch_id==b.id,BlindApplication.state.in_(ACTIVE_APPLICATION_STATES))).all()
    invalidated=[]
    for a in apps:
        a.state='invalid';a.error='盲测输入在揭盲前被改动，申请已失效，需重新申请';invalidated.append(a.id)
    if b.state=='occupied':b.state='sealed'
    log(db,user,'blind_batch.inputs_changed',b.id,'失效申请：'+(','.join(map(str,invalidated)) or '无'))
    db.commit()
    return _public_batch(b)
def create_application(db,user,data):
    b=get_batch(db,data.batch_id)
    if b.state=='revealed':raise HTTPException(409,'该盲测批已揭盲，不再接受新申请；旧研究仍可阅读误差依据')
    active=db.scalar(select(BlindApplication.id).where(BlindApplication.batch_id==b.id,BlindApplication.state.in_(ACTIVE_APPLICATION_STATES)))
    if active:raise HTTPException(409,'该盲测批已被其他研究占用，同一盲测批并发申请只接受一个')
    s=source_or_404(db,data.source_id)
    records=list_records(db,s.id)
    if len(records)<40:raise HTTPException(422,'训练来源至少需要40条样本才能冻结盲测模型')
    _assert_reference_isolation(db,b.references,s.id)
    try:params=validate_parameters(data.parameters)
    except ValueError as e:raise HTTPException(422,str(e))
    # 申请即冻结：Run 行此刻固化训练快照；之后修改训练数据不会改变该模型
    r=Run(source_id=s.id,name='[盲测]'+data.name,parameters=params,snapshot={'source':row(s),'records':records},state='queued',created_at=now())
    db.add(r);db.flush()
    app=BlindApplication(batch_id=b.id,run_id=r.id,name=data.name,applicant=user.email,state='freeze_queued',frozen_input_checksum=b.input_checksum,attempts=0,created_at=now())
    db.add(app)
    try:
        db.flush()
    except Exception:
        db.rollback();raise HTTPException(409,'该盲测批已被其他研究占用，同一盲测批并发申请只接受一个')
    b.state='occupied'
    log(db,user,'blind_app.created',app.id,f'批#{b.id} 模型Run#{r.id} 训练{len(records)}条')
    db.commit()
    app_id=app.id
    try:
        _enqueue('freeze',app_id)
    except HTTPException:
        with Session() as db2:
            a2=db2.get(BlindApplication,app_id);a2.state='failed';a2.error='队列暂不可用，提交未丢失，可失败重试（仍绑定原模型）';db2.commit()
        raise
    with Session() as db3:
        return application_detail(db3,app_id)
def _enqueue(stage,app_id):
    try:queue.rpush(BLIND_QUEUE,json.dumps({'stage':stage,'id':app_id}))
    except Exception:
        raise HTTPException(503,'盲测队列暂不可用，请稍后重试该申请')
def _safe_enqueue(stage,app_id,fail_state,fail_msg):
    try:
        _enqueue(stage,app_id)
    except HTTPException:
        with Session() as db2:
            a2=db2.get(BlindApplication,app_id);a2.state=fail_state;a2.error=fail_msg;db2.commit()
        raise
def list_applications(db):
    return [_public_app(a) for a in db.scalars(select(BlindApplication).order_by(BlindApplication.id.desc()))]
def get_application(db,id):
    a=db.get(BlindApplication,id)
    if not a:raise HTTPException(404,'未找到盲测申请')
    return a
def _public_app(a):
    d=row(a)
    # 揭盲前预测只回传编号与预测值，结构中不含任何标签字段
    return d
def application_detail(db,id):
    return _public_app(get_application(db,id))
def retry_application(db,user,id):
    a=get_application(db,id)
    if a.state not in ('failed',):raise HTTPException(409,'当前状态不允许重试：'+a.state)
    if a.frozen_input_checksum!=get_batch(db,a.batch_id).input_checksum:
        a.state='invalid';a.error='盲测输入已被改动，原申请失效';db.commit();raise HTTPException(409,'盲测输入已被改动，申请已失效，需重新申请')
    # 重试绑定原模型：run_id 不变；冻结成功则从预测阶段继续
    stage='prediction' if a.model_checksum else 'freeze'
    a.state='prediction_queued' if stage=='prediction' else 'freeze_queued'
    a.error='';a.attempts+=1
    log(db,user,'blind_app.retry',a.id,'阶段：'+stage+' · 模型Run#'+str(a.run_id))
    db.commit()
    _safe_enqueue(stage,a.id,'failed','队列暂不可用，可再次失败重试（仍绑定原模型）')
    return application_detail(db,a.id)
def request_unblind(db,user,id):
    a=get_application(db,id)
    if a.state=='revealed':return application_detail(db,a.id)  # 幂等：不重复揭盲
    # 揭盲已在队列或执行中：重复请求直接返回，不再产生第二个揭盲任务
    if a.state in ('unblind_queued','unblind_running'):return application_detail(db,a.id)
    if a.state=='invalid':raise HTTPException(409,'申请已因盲测输入改动失效，不能揭盲')
    if a.state!='awaiting_unblind':raise HTTPException(409,'预测写出后才能申请揭盲，当前状态：'+a.state)
    b=get_batch(db,a.batch_id)
    if a.frozen_input_checksum!=b.input_checksum:
        a.state='invalid';a.error='盲测输入已被改动，申请失效';db.commit();raise HTTPException(409,'盲测输入已被改动，申请已失效')
    a.state='unblind_queued'
    log(db,user,'blind_app.unblind_requested',a.id,'申请一次揭盲')
    db.commit()
    _safe_enqueue('unblind',a.id,'awaiting_unblind','队列暂不可用，揭盲未执行，预测仍保留，可再次申请揭盲')
    return application_detail(db,a.id)
# ---------- worker 侧执行函数（在独立进程调用，不经过 HTTP 标签通道） ----------
def execute_job(stage,app_id):
    from ..config.database import Session
    with Session() as db:
        a=db.get(BlindApplication,app_id)
        if not a:return
        b=db.get(BlindBatch,a.batch_id)
        # 失效/已揭盲任务直接丢弃，保证不重复揭盲
        if a.state in ('invalid','revealed'):return
        if a.frozen_input_checksum!=b.input_checksum:
            a.state='invalid';a.error='盲测输入已被改动，申请失效';log(db,'worker','blind_app.invalidated',a.id);db.commit();return
        try:
            if stage=='freeze':_stage_freeze(db,a,b)
            elif stage=='prediction':_stage_prediction(db,a,b)
            elif stage=='unblind':_stage_unblind(db,a,b)
            db.commit()
        except Exception as e:
            db.rollback()
            a=db.get(BlindApplication,app_id)
            a.state='failed';a.error=str(e)[:2000]
            log(db,'worker','blind_app.failed',app_id,a.error)
            db.commit()
def _stage_freeze(db,a,b):
    if a.model_checksum:  # 已冻结则跳过，崩溃恢复不重复拟合
        _enqueue_prediction(db,a);return
    a.state='freeze_running';db.commit()
    r=db.get(Run,a.run_id)
    bundle=fit_blind_model(r.snapshot['records'],r.parameters)
    (_blind_dir()/f'model-{a.id}.pkl').write_bytes(bundle['pickle'])
    a.model_checksum=bundle['model_checksum'];a.frozen_at=now()
    r.result={'blind':True,'model_checksum':bundle['model_checksum'],'selected_features':bundle['features'],'candidates':bundle['candidates'],'training_checksum':bundle['training_checksum'],'summary':[{'label':'训练样本','value':bundle['training_rows']}],'rows':[],'columns':[],'chart':[],'explanation':'盲测冻结模型：特征选择仅用训练行3折CV；盲测标签全程封存，未参与拟合或选择。'}
    r.state='completed'
    log(db,'worker','blind_app.frozen',a.id,'模型校验和 '+bundle['model_checksum'][:12])
    _enqueue_prediction(db,a)
def _enqueue_prediction(db,a):
    a.state='prediction_queued';db.commit()
    queue.rpush(BLIND_QUEUE,json.dumps({'stage':'prediction','id':a.id}))
def _stage_prediction(db,a,b):
    if a.predictions:  # 预测已写出则不重复
        a.state='awaiting_unblind';db.commit();return
    a.state='prediction_running';db.commit()
    import pickle
    model=pickle.loads((_blind_dir()/f'model-{a.id}.pkl').read_bytes())
    r=db.get(Run,a.run_id)
    features=r.result['selected_features']
    bundle={'model':model,'features':features}
    rows,checksum=predict_blind(bundle,b.inputs)
    a.predictions=rows;a.predictions_checksum=checksum;a.predicted_at=now()
    a.state='awaiting_unblind'
    log(db,'worker','blind_app.predicted',a.id,f'{len(rows)}条预测已写出，等待揭盲')
def _stage_unblind(db,a,b):
    if a.state=='revealed':return
    if a.state!='unblind_queued':return
    if not a.predictions:raise RuntimeError('预测尚未写出，不能揭盲')
    a.state='unblind_running';db.commit()
    labels=_load_labels(b.labels_path)
    if len(labels)!=len(a.predictions):raise RuntimeError('封存标签与预测条数不一致')
    rows=[]
    for p,label in zip(a.predictions,labels):
        rows.append({'reference':p['reference'],'actual_minutes':float(label),'predicted_minutes':p['predicted_minutes'],'error_minutes':float(p['predicted_minutes']-label)})
    m=unblind_metrics(rows)
    result={'summary':[{'label':'盲测 MAE / min','value':m['mae']},{'label':'盲测 RMSE / min','value':m['rmse']},{'label':'盲测样本','value':m['n']}],'rows':rows,'columns':['reference','actual_minutes','predicted_minutes','error_minutes'],'model_checksum':a.model_checksum,'predictions_checksum':a.predictions_checksum,'input_checksum':b.input_checksum,'labels_checksum':b.labels_checksum,'explanation':'唯一一次揭盲后的独立盲测误差：模型在训练行内选特征并冻结，盲测标签此前对所有查看与计算不可见。'}
    a.result=result;a.state='revealed';a.revealed_at=now()
    b.state='revealed';b.revealed_at=now();b.result=result
    log(db,'worker','blind_app.revealed',a.id,f"MAE={m['mae']:.4f} RMSE={m['rmse']:.4f}")
def recover_stale():
    # 启动恢复：running 中断的任务回到对应 queued，不重复揭盲、不重复预测
    from ..config.database import Session
    with Session() as db:
        for a in db.scalars(select(BlindApplication)):
            mapping={'freeze_running':'freeze','prediction_running':'prediction','unblind_running':'unblind'}
            if a.state in mapping:
                queue.rpush(BLIND_QUEUE,json.dumps({'stage':mapping[a.state],'id':a.id}))
                a.state={'freeze_running':'freeze_queued','prediction_running':'prediction_queued','unblind_running':'unblind_queued'}[a.state]
        db.commit()
def blind_csv(db,id):
    a=get_application(db,id)
    if a.state!='revealed':raise HTTPException(409,'揭盲后才能导出盲测误差明细')
    from .export import csv_response
    return csv_response(a.result,'blind-'+str(id)+'.csv')
def assert_new_record_isolated(db,source_id,reference):
    # 训练样本新增时校验：试件编号不得与任何盲测批重合（按试件编号隔离）
    for b in db.scalars(select(BlindBatch).order_by(BlindBatch.id)):
        if reference in b.references:raise HTTPException(422,'试件编号已属于盲测批，训练与盲测必须按试件编号隔离：'+reference)
