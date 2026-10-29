# 03 步频模拟

入口为[step_sim.py](../../code/run-sim/step_sim.py#L196)及自建[StepLab instrumentation](../../code/run-sim/step-helper/InputInstrumentation.java#L183)。目标应用保持原安装包，独立helper在受控测试环境中调用平台传感器注入。平台模式及实际接受情况必须验证；不能只凭宿主进程存在认定注入成功。

本次成功输入使用50Hz采样、加速度幅值6m/s²、基础步频180次/分钟及6次/分钟范围的小幅平滑变化。Java波形按单调时钟生成X/Y/Z加速度，并记录attempted/accepted；accepted持续增长、run_id匹配和实际APP步频读回共同作为观测依据。APP最终平均步频178不等于波形参数恒为178，也不证明APP内部计步算法已知。

本轮最初传感器段请求1100秒，等待/暂停也消耗预算，之后在同一APP会话续400秒段，实际续段约267秒后停止。1800秒是随后整理的准备/运行覆盖预算建议，不应伪称本轮一开始就使用1800秒。`step_continue.py`核验前段身份后续接，不能重复instrumentation或在活跃helper下面恢复NORMAL。

公开副本对默认传感器输入增加未配置拒绝护栏；`--stop`仍可用于本机受控清理。结束必须优雅停本轮helper，确认已不活跃，再读回SensorService NORMAL。签名key和实际run_id不在本包中。

参考：[Android运动传感器](https://developer.android.com/develop/sensors-and-location/sensors/sensors_motion)。
