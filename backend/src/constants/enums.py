from enum import Enum
class Role(str,Enum):
    admin='admin'; analyst='analyst'; viewer='viewer'
class RunState(str,Enum):
    queued='queued'; running='running'; completed='completed'; failed='failed'
class ReviewState(str,Enum):
    draft='draft'; approved='approved'
class BlindBatchState(str,Enum):
    # 封存：标签封存，任何特征选择/调参/普通查看都接触不到实测时长
    sealed='sealed'
    # 占用：已有研究申请揭盲，同批并发申请只接受一个
    occupied='occupied'
    # 已揭盲：唯一一次揭盲已执行，误差依据可被旧研究阅读
    revealed='revealed'
class BlindApplicationState(str,Enum):
    # 冻结模型排队/冻结中
    freeze_queued='freeze_queued'; freeze_running='freeze_running'
    # 模型已冻结、预测排队/预测中；预测写出后可恢复、不重复揭盲
    prediction_queued='prediction_queued'; prediction_running='prediction_running'
    # 预测已写出，等待申请一次揭盲
    awaiting_unblind='awaiting_unblind'
    # 揭盲排队/揭盲中
    unblind_queued='unblind_queued'; unblind_running='unblind_running'
    # 已揭盲：误差依据落定
    revealed='revealed'
    # 冻结或预测失败：重试仍绑定原模型记录
    failed='failed'
    # 盲测输入在揭盲前被改动，申请失效
    invalid='invalid'
