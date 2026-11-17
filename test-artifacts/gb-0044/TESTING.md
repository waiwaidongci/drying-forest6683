# 独立盲测批测试说明

本目录只放测试，不混入源码。两个脚本均使用 SQLite + 进程内数据，不依赖 Docker / PostgreSQL / Redis；worker 测试用内存假队列驱动真实 `src.worker` 主循环。

## 运行方式

依赖装在仓库外目录（避免污染项目），仅需一次：

```bash
# 1) 引导 pip（系统无 ensurepip 时）
mkdir -p /tmp/pyboot /tmp/pylibs
curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/pyboot/get-pip.py
python3 /tmp/pyboot/get-pip.py --target /tmp/pyboot
# 2) 装后端依赖与测试客户端
PYTHONPATH=/tmp/pyboot python3 -m pip install --target /tmp/pylibs -r backend/requirements.txt httpx
```

在 `backend/` 目录执行：

```bash
# 端到端：HTTP 全流程 + 真实并发竞态（62 项断言）
DATABASE_URL=sqlite:////tmp/blind_e2e.db ARTIFACT_DIR=/tmp/blind_e2e_artifacts \
SEED_DEMO=false PYTHONPATH=/tmp/pylibs \
python3 ../test-artifacts/gb-0044/blind_e2e.py

# worker 集成：BLPOP 双队列分发、自动推进、崩溃恢复与幂等
DATABASE_URL=sqlite:////tmp/blind_worker.db ARTIFACT_DIR=/tmp/blind_worker_artifacts \
SEED_DEMO=false PYTHONPATH=/tmp/pylibs \
python3 ../test-artifacts/gb-0044/worker_integration.py
```

## 覆盖的需求点

| 需求 | 对应断言位置 |
|---|---|
| 实测时长封存，选特征/调参/普通查看接触不到标签 | §3：响应、列表、详情、数据库行均无 `minutes`；标签仅写入 `0600` 工件 |
| 训练样本与盲测样本按试件编号隔离 | §5：双向撞编号 422；封存即校验、训练新增即校验 |
| 先冻结可复现模型，再申请一次揭盲 | §1 校验和可复现；§7 冻结→预测→待揭盲→揭盲 |
| 同批并发只接受一个 | §6 服务层 409 + 数据库部分唯一索引；§6b 8 线程真实竞态恰好 1 个成功 |
| 失败重试仍绑定原模型 | §10：重试 `run_id` 不变，确定性模型校验和一致 |
| 写出预测后中断可恢复、不重复揭盲 | §11 与 worker 集成：恢复重入队，预测/揭盲均幂等 |
| 揭盲前改训练数据不改变已冻结模型 | §8：快照固化，重放冻结校验和不变 |
| 改动盲测输入使申请失效 | §9：未揭盲申请置 `invalid`、批次释放回封存、重试/揭盲被拒 |
| 页面区分封存/占用/已揭盲并展示误差依据，旧研究仍可读 | §7、§9、§12：三态、误差行/CSV、历史 Run 与审计可读 |

## 状态机

盲测批 `BlindBatch.state`：`sealed`（封存）→ `occupied`（占用）→ `revealed`（已揭盲）。

盲测申请 `BlindApplication.state`：

```text
freeze_queued → freeze_running → prediction_queued → prediction_running
→ awaiting_unblind → unblind_queued → unblind_running → revealed
        └────────────── 任一阶段异常 → failed（重试绑定原 run_id）
        └────────────── 揭盲前输入被改 → invalid（不可重试，需重新申请）
```

## 数据与隔离口径

- 盲测输入只接受 `moisture/thickness/temperature/airflow` 四个特征列；出现 `minutes` 或未知字段一律 422，标签走独立 `labels` 通道。
- 标签只以 `ARTIFACT_DIR/blind/labels-{batch_id}.json`（权限 0600）封存，数据库不存标签、不回传工件路径。
- 冻结模型序列化为 `model-{application_id}.pkl`；申请持有独立 `Run` 快照，后续训练数据变更不影响该模型。
