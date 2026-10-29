# 04 里程累计：输入器与APP是两条证据

## 输入器的已知计算

[mumu_route.send](../../code/run-sim/mumu_route.py#L87)计算`distance = min(target_distance, speed * active_elapsed)`。这是宿主轨迹输入器的指令累计量。随后[position插值](../../code/run-sim/mumu_route.py#L41)把这个累计量映射到闭合路线上的经纬度，最后调用MuMu CLI发送位置。暂停冻结有效模拟时间，离线或等待GO不能直接计入APP有效运动时间。

本次轨迹输入预算为3100米，速度约3.3333m/s，所以计划推进约930秒。APP会话读回18分40秒，包含其自身时序和人工准备/暂停等过程；两种时钟不应混为同一时间来源。

## APP结果的读回

[mumu_app_ui.parse_metrics](../../code/run-sim/mumu_app_ui.py#L47)从新鲜只读UI树读取`tvValidDistance`（展开面板对应`tvValidDistance2`）及步频、配速和点位。这个代码读取界面文本，不是APP的内部距离累计源码，也没有修改APP的累计逻辑。

APP可能有采样、过滤、地图、速度和后台校验等处理，但本项目没有其内部源码，不能断言具体算法。本次应用显示3.00km、5/5和成绩达标才作为结果；不能因为输入器发送3100米就宣称APP记录必然有效。原始成绩截图/日志保留在本机私有归档，公共库仅给匿名摘要。

后续主机监测允许输入器完成后等待最多20秒让APP更新末尾距离，不追加位置来掩盖读回延迟。APP结束与保存仍由用户手工完成。
