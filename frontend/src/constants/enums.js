export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const TicketStatus={pending:'待评估',open:'异常中',recovered:'已恢复',revoked:'已撤销'};
export const DayState={normal:'正常',exceed:'超出阈值',missing:'缺日',pending:'无基线'};
export const CloseReason={auto_recovered:'连续两日恢复关闭',streak_broken:'未达开单阈值',baseline_switch:'基线换版结案',replay_revoked:'重演撤销误报',manual_revoked:'人工撤销误报','':'—'};
export const EventKind={created:'重演登记',opened:'开具异常',recovered:'恢复关闭',revoked:'撤销误报',reopened:'重新打开',adjusted:'重演调整',note:'人工处置',manual_revoked:'人工撤销'};
