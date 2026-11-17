"""独立盲测批端到端测试：SQLite + 内存假队列，驱动真实 service/worker 阶段函数。

运行（在 backend 目录）：
  DATABASE_URL=sqlite:////tmp/blind_e2e.db ARTIFACT_DIR=/tmp/blind_e2e_artifacts \
  SEED_DEMO=false PYTHONPATH=/tmp/pylibs python3 ../test-artifacts/gb-0044/blind_e2e.py
"""
import os,sys,json,math,tempfile,pathlib
sys.path.insert(0,os.getcwd())
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config.database import Base,engine,Session
from src.main import app
from src.models.user import User
from src.models.blind_batch import BlindBatch
from src.models.blind_application import BlindApplication
from src.services import blind
from src.services.algorithm import fit_blind_model,predict_blind
from src.utils.security import hash_password
from src.utils.serialization import now

PASS=[];FAIL=[]
def check(name,cond,detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✓ ' if cond else '  ✗ ')+name+((' — '+detail) if detail and not cond else ''))

class FakeQueue:
    def __init__(self):self.items=[]
    def rpush(self,k,v):self.items.append((k,v))
    def ping(self):return True
    def blpop(self,keys,timeout=2):return None

fake=FakeQueue()
blind.queue=fake

def drain():
    while fake.items:
        key,value=fake.items.pop(0)
        payload=json.loads(value)
        blind.execute_job(payload['stage'],payload['id'])

def gen_record(rng,i,prefix):
    moisture=round(20+rng.random()*40,2);thickness=round(1+rng.random()*7,2)
    temperature=round(50+rng.random()*50,2);airflow=round(1+rng.random()*4,2)
    minutes=round(12+1.6*moisture+8*thickness-.45*temperature-3*airflow+0.1*moisture*thickness+rng.gauss(0,3),2)
    return {'reference':f'{prefix}-{i:04d}','values':{'moisture':moisture,'thickness':thickness,'temperature':temperature,'airflow':airflow,'minutes':max(5,minutes)}}

import random
rng=random.Random(7)
with Session() as db:
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    for role in ('admin','analyst','viewer'):
        db.add(User(email=role+'@x.demo',password_hash=hash_password('pw123456'),role=role))
    db.commit()

with TestClient(app) as client:
    def login(role):
        r=client.post('/auth/login',json={'email':role+'@x.demo','password':'pw123456'})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['token']}
    admin=login('admin');analyst=login('analyst');viewer=login('viewer')

    print('\n== 1. 算法 oracle：冻结可复现，与盲测标签无关 ==')
    train=[gen_record(rng,i,'TRAIN') for i in range(1,61)]
    b1=fit_blind_model(train,{'trees':60,'depth':6})
    b2=fit_blind_model(train,{'trees':60,'depth':6})
    check('同一训练输入两次冻结模型校验和一致',b1['model_checksum']==b2['model_checksum'])
    blind_inputs=[{'reference':f'BLIND-{i:02d}','values':{k:round(20+rng.random()*40,2) if k=='moisture' else round(1+rng.random()*7,2) if k=='thickness' else round(50+rng.random()*50,2) if k=='temperature' else round(1+rng.random()*4,2) for k in ('moisture','thickness','temperature','airflow')}} for i in range(1,21)]
    p1,_=predict_blind(b1,blind_inputs);p2,_=predict_blind(b2,blind_inputs)
    check('可复现模型预测完全一致',[x['predicted_minutes'] for x in p1]==[x['predicted_minutes'] for x in p2])

    print('\n== 2. 训练来源与样本 ==')
    r=client.post('/sources',headers=analyst,json={'name':'训练来源A','filename':'train-a.csv','description':'d'})
    assert r.status_code==200,r.text;source_id=r.json()['id']
    r=client.post(f'/sources/{source_id}/records',headers=analyst,json={'records':train})
    check('写入60条训练样本',r.status_code==200 and r.json()['added']==60,r.text)

    print('\n== 3. 封存盲测批：标签不入库、不回显 ==')
    labels=[round(max(5,12+1.6*x['values']['moisture']+8*x['values']['thickness']-.45*x['values']['temperature']-3*x['values']['airflow']+rng.gauss(0,3)),2) for x in blind_inputs]
    payload={'name':'十一月独立盲测批','inputs':blind_inputs,'labels':labels}
    r=client.post('/blind/batches',headers=analyst,json=payload)
    check('封存成功',r.status_code==200,r.text);batch_id=r.json()['id']
    body=r.json()
    check('封存响应不含标签字段','minutes' not in json.dumps(body,ensure_ascii=False) and 'labels' not in body)
    check('封存响应不暴露标签工件路径','labels_path' not in body)
    r=client.get(f'/blind/batches/{batch_id}',headers=viewer)
    check('阅读者可查看盲测批但看不到标签',r.status_code==200 and 'minutes' not in json.dumps(r.json(),ensure_ascii=False))
    r=client.get('/blind/batches',headers=viewer)
    check('盲测批列表无标签泄漏',r.status_code==200 and all('minutes' not in json.dumps(x,ensure_ascii=False) for x in r.json()))
    label_file=pathlib.Path(os.environ['ARTIFACT_DIR'])/'blind'/f'labels-{batch_id}.json'
    check('标签仅封存为0600受限工件',label_file.exists() and (label_file.stat().st_mode & 0o777)==0o600)
    with Session() as db:
        row=db.get(BlindBatch,batch_id)
        check('数据库行不持有标签明文',all('minutes' not in json.dumps(x) for x in row.inputs))
        check('初始状态为封存 sealed',row.state=='sealed')

    print('\n== 4. 输入通道防泄漏与口径校验 ==')
    ten_inputs=blind_inputs[:10];ten_labels=labels[:10]
    leak=[{'reference':f'L-{i:02d}','values':{**ten_inputs[i]['values']}} for i in range(10)]
    leak[0]['values']['minutes']=99.0
    r=client.post('/blind/batches',headers=analyst,json={'name':'泄漏批','inputs':leak,'labels':ten_labels})
    check('输入夹带实测时长被422拒绝',r.status_code==422)
    bad=[{'reference':f'B-{i:02d}','values':{**ten_inputs[i]['values']}} for i in range(10)]
    bad[0]['values']['unknown']=1
    r=client.post('/blind/batches',headers=analyst,json={'name':'未知字段批','inputs':bad,'labels':ten_labels})
    check('未知特征字段被422拒绝',r.status_code==422)
    r=client.post('/blind/batches',headers=analyst,json={'name':'标签条数不符','inputs':blind_inputs,'labels':labels[:-1]})
    check('标签条数不一致被422拒绝',r.status_code==422)
    dup=blind_inputs[:10]+[dict(blind_inputs[0])]
    r=client.post('/blind/batches',headers=analyst,json={'name':'编号重复批','inputs':dup,'labels':[1.0]*11})
    check('批内试件编号重复被422拒绝',r.status_code==422)
    r=client.post('/blind/batches',headers=analyst,json={'name':'过小批','inputs':blind_inputs[:5],'labels':[1.0]*5})
    check('不足10条的盲测批被422拒绝',r.status_code==422)

    print('\n== 5. 按试件编号隔离 ==')
    r=client.post(f'/sources/{source_id}/records',headers=analyst,json={'records':[{'reference':blind_inputs[0]['reference'],'values':train[0]['values']}]})
    check('训练样本编号撞上盲测批被422拒绝',r.status_code==422,r.text)
    other_source=client.post('/sources',headers=analyst,json={'name':'训练来源B','filename':'b.csv','description':'d'}).json()['id']
    overlap_inputs=[{'reference':train[0]['reference'],'values':blind_inputs[0]['values']}]+blind_inputs[1:11]
    r=client.post('/blind/batches',headers=analyst,json={'name':'编号撞训练批','inputs':overlap_inputs,'labels':labels[:11]})
    check('盲测批编号撞上训练样本被422拒绝',r.status_code==422)
    r=client.post('/blind/applications',headers=analyst,json={'batch_id':batch_id,'source_id':other_source,'name':'空来源申请','parameters':{'trees':60,'depth':6}})
    check('训练样本不足40条被422拒绝',r.status_code==422)

    print('\n== 6. 申请冻结：同批并发只接受一个 ==')
    r=client.post('/blind/applications',headers=viewer,json={'batch_id':batch_id,'source_id':source_id,'name':'阅读者申请','parameters':{'trees':60,'depth':6}})
    check('阅读者无权申请(403)',r.status_code==403)
    r=client.post('/blind/applications',headers=analyst,json={'batch_id':batch_id,'source_id':source_id,'name':'盲测研究甲','parameters':{'trees':60,'depth':6}})
    check('首个申请受理',r.status_code==200,r.text);app_id=r.json()['id'];run_id=r.json()['run_id']
    check('受理后批次转为占用 occupied',client.get(f'/blind/batches/{batch_id}',headers=analyst).json()['state']=='occupied')
    r=client.post('/blind/applications',headers=admin,json={'batch_id':batch_id,'source_id':source_id,'name':'盲测研究乙并发','parameters':{'trees':80,'depth':8}})
    check('并发申请被409拒绝',r.status_code==409)
    with Session() as db:
        raw=BlindApplication(batch_id=batch_id,run_id=run_id,name='raw',applicant='z',state='freeze_queued',frozen_input_checksum='c',created_at=now())
        db.add(raw)
        raised=False
        try:db.commit()
        except IntegrityError:raised=True;db.rollback()
        check('数据库部分唯一索引兜底并发占用',raised)

    print('\n== 6b. 真实并发：8 线程同时申请同一盲测批，只接受一个 ==')
    conc_batch=client.post('/blind/batches',headers=analyst,json={'name':'并发竞态批','inputs':blind_inputs,'labels':labels}).json()
    import threading
    barrier=threading.Barrier(8);results=[];lock=threading.Lock()
    def concurrent_apply(i):
        barrier.wait()
        r=client.post('/blind/applications',headers=analyst,json={'batch_id':conc_batch['id'],'source_id':source_id,'name':f'并发研究{i}','parameters':{'trees':60,'depth':6}})
        with lock:results.append(r.status_code)
    threads=[threading.Thread(target=concurrent_apply,args=(i,)) for i in range(8)]
    [t.start() for t in threads];[t.join() for t in threads]
    check('8个并发申请恰好1个成功',results.count(200)==1 and results.count(409)==7,str(results))
    with Session() as db:
        active=db.scalars(select(BlindApplication).where(BlindApplication.batch_id==conc_batch['id'])).all()
        check('该批只存在一条有效申请记录',len(active)==1 and active[0].state in ('freeze_queued','prediction_queued','prediction_running','awaiting_unblind'))

    print('\n== 7. worker 阶段：冻结→预测→等待揭盲，全程无标签 ==')
    drain()
    app_detail=client.get(f'/blind/applications/{app_id}',headers=analyst).json()
    check('预测写出后进入 awaiting_unblind',app_detail['state']=='awaiting_unblind',app_detail['state'])
    check('揭盲前预测行只有编号与预测值',all(set(x.keys())=={'reference','predicted_minutes'} for x in app_detail['predictions']))
    check('模型工件已落盘',(pathlib.Path(os.environ['ARTIFACT_DIR'])/'blind'/f'model-{app_id}.pkl').exists())
    r=client.post(f'/blind/applications/{app_id}/unblind',headers=analyst)
    check('预测写出后可申请一次揭盲',r.status_code==200)
    check('揭盲请求已入队',len(fake.items)==1)
    # 重复申请不产生第二个揭盲任务（幂等：只接受一次揭盲）
    r=client.post(f'/blind/applications/{app_id}/unblind',headers=analyst)
    check('揭盲接口幂等，不重复揭盲',r.status_code==200 and len(fake.items)==1)
    drain()
    final=client.get(f'/blind/applications/{app_id}',headers=analyst).json()
    check('申请进入 revealed',final['state']=='revealed')
    batch_final=client.get(f'/blind/batches/{batch_id}',headers=analyst).json()
    check('批次进入 revealed 且误差依据落定',batch_final['state']=='revealed' and batch_final['result']['summary'][0]['label']=='盲测 MAE / min')
    check('揭盲后误差行包含实测/预测/误差三列',set(final['result']['columns'])=={'reference','actual_minutes','predicted_minutes','error_minutes'})
    check('误差行数与盲测样本一致',len(final['result']['rows'])==20)
    r=client.get(f'/blind/applications/{app_id}/export',headers=viewer)
    check('阅读者可导出揭盲后CSV',r.status_code==200 and 'text/csv' in r.headers['content-type'])
    r=client.put(f'/blind/batches/{batch_id}/inputs',headers=analyst,json={'inputs':blind_inputs})
    check('已揭盲批输入不可改动(409)',r.status_code==409)
    r=client.post('/blind/applications',headers=analyst,json={'batch_id':batch_id,'source_id':source_id,'name':'再次申请','parameters':{'trees':60,'depth':6}})
    check('已揭盲批不再接受新申请(409)',r.status_code==409)

    print('\n== 8. 冻结后修改训练数据不改变已冻结模型 ==')
    checksum_before=final['model_checksum']
    new_rec=gen_record(rng,9001,'TRAIN')
    r=client.post(f'/sources/{source_id}/records',headers=analyst,json={'records':[new_rec]})
    check('冻结后仍可追加训练样本(编号不撞盲测)',r.status_code==200,r.text)
    run_detail=client.get(f'/runs/{run_id}',headers=analyst).json()
    check('冻结Run快照仍是原60条训练行',len(run_detail['snapshot']['records'])==60)
    # 重放冻结阶段：已冻结模型被跳过（崩溃恢复不重复拟合），模型不变
    fake.items.append((blind.BLIND_QUEUE,json.dumps({'stage':'freeze','id':app_id})));drain()
    check('重放冻结不改变模型校验和',client.get(f'/blind/applications/{app_id}',headers=analyst).json()['model_checksum']==checksum_before)

    print('\n== 9. 改动盲测输入使申请失效 ==')
    nb=client.post('/blind/batches',headers=analyst,json={'name':'失效实验批','inputs':blind_inputs,'labels':labels}).json()
    a2=client.post('/blind/applications',headers=analyst,json={'batch_id':nb['id'],'source_id':source_id,'name':'失效研究','parameters':{'trees':60,'depth':6}}).json()
    drain()
    a2=client.get(f'/blind/applications/{a2["id"]}',headers=analyst).json()
    check('二号申请预测完成待揭盲',a2['state']=='awaiting_unblind')
    changed=[dict(x,values={**x['values'],'airflow':round(x['values']['airflow']+0.5,2)}) for x in blind_inputs]
    r=client.put(f'/blind/batches/{nb["id"]}/inputs',headers=analyst,json={'inputs':changed})
    check('改动输入成功',r.status_code==200)
    a2=client.get(f'/blind/applications/{a2["id"]}',headers=analyst).json()
    check('未揭盲申请被置为 invalid',a2['state']=='invalid')
    check('批次释放回 sealed',client.get(f'/blind/batches/{nb["id"]}',headers=analyst).json()['state']=='sealed')
    r=client.post(f'/blind/applications/{a2["id"]}/retry',headers=analyst)
    check('失效申请重试被409拒绝',r.status_code==409)
    r=client.post(f'/blind/applications/{a2["id"]}/unblind',headers=analyst)
    check('失效申请不能揭盲(409)',r.status_code==409)
    # 失效后允许重新申请（旧研究仍可读）
    r=client.post('/blind/applications',headers=analyst,json={'batch_id':nb['id'],'source_id':source_id,'name':'重新申请','parameters':{'trees':60,'depth':6}})
    check('失效后可重新申请',r.status_code==200)
    old=client.get(f'/blind/applications/{a2["id"]}',headers=viewer)
    check('旧失效研究仍可被阅读者读取',old.status_code==200 and old.json()['state']=='invalid')

    print('\n== 10. 失败重试绑定原模型（同一 run_id，确定性校验和） ==')
    nb2=client.post('/blind/batches',headers=analyst,json={'name':'重试实验批','inputs':blind_inputs,'labels':labels}).json()
    a3=client.post('/blind/applications',headers=analyst,json={'batch_id':nb2['id'],'source_id':source_id,'name':'重试研究','parameters':{'trees':60,'depth':6}}).json()
    drain()
    a3=client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()
    frozen_checksum=a3['model_checksum']
    # 模拟 worker 在揭盲前崩溃：人工置 failed 并删除模型工件，模拟冻结级失败
    with Session() as db:
        row=db.get(BlindApplication,a3['id']);row.state='failed';row.error='模拟冻结后崩溃';row.model_checksum='';db.commit()
    pkl=pathlib.Path(os.environ['ARTIFACT_DIR'])/'blind'/f'model-{a3["id"]}.pkl'
    if pkl.exists():pkl.unlink()
    r=client.post(f'/blind/applications/{a3["id"]}/retry',headers=analyst)
    check('失败申请可重试',r.status_code==200,r.text)
    retry_body=r.json()
    check('重试仍绑定原 run_id',retry_body['run_id']==a3['run_id'])
    check('重试次数累加',retry_body['attempts']==1)
    drain()
    a3b=client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()
    check('重试后重新冻结出同一确定性模型',a3b['model_checksum']==frozen_checksum)
    check('重试后自动推进到待揭盲',a3b['state']=='awaiting_unblind')

    print('\n== 11. 中断可恢复：不重复预测、不重复揭盲 ==')
    # 预测阶段中断
    with Session() as db:
        row=db.get(BlindApplication,a3b['id']);row.state='prediction_running';row.predictions=None;db.commit()
    blind.recover_stale()
    check('恢复把 prediction_running 重新入队',any(json.loads(v)['stage']=='prediction' for _,v in fake.items))
    drain()
    a3c=client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()
    check('恢复后预测只写一次并回到待揭盲',a3c['state']=='awaiting_unblind' and len(a3c['predictions'])==20)
    # 揭盲阶段中断
    client.post(f'/blind/applications/{a3["id"]}/unblind',headers=analyst)
    with Session() as db:
        row=db.get(BlindApplication,a3b['id']);row.state='unblind_running';db.commit()
    blind.recover_stale();drain()
    a3d=client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()
    check('揭盲中断恢复后正常 revealed',a3d['state']=='revealed')
    # 再次恢复/重放揭盲不得产生第二次揭盲
    before_revealed_at=a3d['revealed_at']
    blind.execute_job('unblind',a3d['id'])
    a3e=client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()
    check('重放揭盲被幂等忽略',a3e['state']=='revealed' and a3e['revealed_at']==before_revealed_at)
    # 预测幂等：重放 prediction 不覆盖已揭盲结果
    blind.execute_job('prediction',a3d['id'])
    check('已揭盲申请不受预测重放影响',client.get(f'/blind/applications/{a3["id"]}',headers=analyst).json()['state']=='revealed')

    print('\n== 12. 旧研究与审计仍可读 ==')
    r=client.get('/runs',headers=viewer)
    check('全部历史 Run（含盲测冻结Run）仍可读',r.status_code==200 and any(x['id']==run_id for x in r.json()))
    r=client.get('/audit',headers=viewer)
    actions={x['action'] for x in r.json()}
    check('审计包含封存/申请/揭盲事件',{'blind_batch.sealed','blind_app.created','blind_app.revealed'} <= actions)

print(f'\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
if FAIL:
    print('失败项：');[print(' -',x) for x in FAIL];sys.exit(1)
print('全部通过')
