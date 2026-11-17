export const roles={admin:'负责人',analyst:'分析师',viewer:'业务阅读者'};
export const runStates={queued:'队列中',running:'正在计算',completed:'已完成',failed:'失败'};
export const reviewStates={draft:'待复核',approved:'已批准'};
export const blindBatchStates={sealed:'封存',occupied:'占用',revealed:'已揭盲'};
export const blindAppStates={
  freeze_queued:'冻结排队',freeze_running:'冻结模型中',
  prediction_queued:'预测排队',prediction_running:'写出预测中',
  awaiting_unblind:'待揭盲',
  unblind_queued:'揭盲排队',unblind_running:'揭盲中',
  revealed:'已揭盲',failed:'失败',invalid:'已失效'
};
