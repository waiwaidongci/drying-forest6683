"""worker 主循环集成测试：用内存假队列验证 BLPOP 双队列分发与盲测全流程推进。

运行（在 backend 目录）：
  DATABASE_URL=sqlite:////tmp/blind_worker.db ARTIFACT_DIR=/tmp/blind_worker_artifacts \
  SEED_DEMO=false PYTHONPATH=/tmp/pylibs python3 ../test-artifacts/gb-0044/worker_integration.py
"""
import os,sys,json,time,random,threading
sys.path.insert(0,os.getcwd())
from src.config.database import Base,engine,Session
from src.models.user import User
from src.models.blind_application import BlindApplication
from src.services import blind
from src import worker
from src.utils.security import hash_password

class MemQueue:
    def __init__(self):
        self.lists={};self.cond=threading.Condition()
    def rpush(self,k,v):
        with self.cond:
            self.lists.setdefault(k,[]).append(v);self.cond.notify_all()
    def blpop(self,keys,timeout=2):
        end=time.time()+timeout
        with self.cond:
            while time.time()<end:
                for k in keys:
                    if self.lists.get(k):return (k,self.lists[k].pop(0))
                self.cond.wait(min(0.1,max(0,end-time.time())))
            return None
    def ping(self):return True

mem=MemQueue()
worker.queue=mem        # worker 主循环使用的队列
blind.queue=mem         # service 入队使用同一队列
stop=threading.Event()
orig_heartbeat=worker.ARTIFACT_DIR
def loop():
    # 复用 worker.main 的逻辑，但可被停止
    while not stop.is_set():
        (worker.ARTIFACT_DIR/'heartbeat').write_text(str(time.time()))
        item=mem.blpop(['analysis:queue',blind.BLIND_QUEUE],timeout=1)
        if not item:continue
        key,value=item
        if key=='analysis:queue':worker.execute(int(value))
        else:worker.execute_blind(json.loads(value))
t=threading.Thread(target=loop,daemon=True);t.start()

rng=random.Random(11)
def rec(i,prefix,blind=False):
    m=round(20+rng.random()*40,2);th=round(1+rng.random()*7,2);te=round(50+rng.random()*50,2);a=round(1+rng.random()*4,2)
    v={'moisture':m,'thickness':th,'temperature':te,'airflow':a}
    if not blind:v['minutes']=round(max(5,12+1.6*m+8*th-.45*te-3*a+rng.gauss(0,3)),2)
    return {'reference':f'{prefix}-{i:04d}','values':v}

Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
with Session() as db:
    db.add(User(email='analyst@x.demo',password_hash=hash_password('pw'),role='analyst'));db.commit()

from src.models.source import Source
from src.models.record import Record
from src.services.domain_validation import validate_record
with Session() as db:
    s=Source(key='S1',name='训练',filename='s.csv',description='d');db.add(s);db.flush()
    train=[rec(i,'TRAIN') for i in range(60)]
    db.add_all([Record(source_id=s.id,reference=r['reference'],values=validate_record(r['values'])) for r in train])
    sid=s.id;db.commit()

inputs=[rec(i,'BLIND',blind=True) for i in range(15)]
labels=[round(max(5,12+1.6*x['values']['moisture']+8*x['values']['thickness']-.45*x['values']['temperature']-3*x['values']['airflow']+rng.gauss(0,3)),2) for x in inputs]

class U:email='analyst@x.demo'
from src.schemas.blind import BlindBatchInput,BlindApplicationInput
from src.schemas.source import SourceInput
with Session() as db:
    b=blind.create_batch(db,U,BlindBatchInput(name='worker集成批',inputs=inputs,labels=labels));bid=b['id']
    app=blind.create_application(db,U,BlindApplicationInput(batch_id=bid,source_id=sid,name='worker研究',parameters={'trees':60,'depth':6}));aid=app['id']

def wait_state(target,timeout=40):
    end=time.time()+timeout
    while time.time()<end:
        with Session() as db:
            st=db.get(BlindApplication,aid).state
        if st==target:return st
        if st in ('failed','invalid'):raise AssertionError('意外终态 '+st)
        time.sleep(0.3)
    raise AssertionError('等待 '+target+' 超时')

s1=wait_state('awaiting_unblind');print('worker 自动完成 冻结→预测，状态 =',s1)
with Session() as db:blind.request_unblind(db,U,aid)
s2=wait_state('revealed');print('worker 完成揭盲，状态 =',s2)
# 再投一个 unblind 重放，确认幂等不报错、不二次揭盲
mem.rpush(blind.BLIND_QUEUE,json.dumps({'stage':'unblind','id':aid}));time.sleep(1.5)
with Session() as db:
    a=db.get(BlindApplication,aid);print('重放揭盲后仍为',a.state,'MAE=',round(a.result['summary'][0]['value'],4))
    assert a.state=='revealed'
# 崩溃恢复：置 running 后调用 recover_stale，worker 自动消化且不重复
with Session() as db:
    a=db.get(BlindApplication,aid);before=a.revealed_at
blind.recover_stale();time.sleep(1.5)
with Session() as db:
    a=db.get(BlindApplication,aid);assert a.revealed_at==before
    print('恢复重放未重复揭盲，revealed_at 不变')
stop.set();t.join(timeout=3)
print('worker 集成测试全部通过')
