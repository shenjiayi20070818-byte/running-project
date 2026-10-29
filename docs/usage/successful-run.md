# 成功分步流程与复现

这是成功会话的代码归档与操作说明，含后续宿主修复，不是未经验证的新全自动脚本。主机输入器负责GPS/步频，APP中的开始、暂停、结束、保存由用户操作。

## 前置准备

在仓库外私有工作目录恢复已审核配置、目标原APK和helper签名/依赖。准备Windows MuMu、Python、Android SDK platform-tools、build-tools35和JDK；源码可通过三个build脚本构建GPS/Step/UI helper。公库没有APK、密钥、SDK或真实配置，不会开箱移动。只允许版本/实例/当前账号/当日布局和两种坐标参考核验一致后运行，权限与登录由用户处理。

## 执行顺序

1. 用`mumu_diagnose.py`和`mumu_start_position.py --check-only`只读核对环境与起点，检查GPS持续更新、卫星与位置容差。当前配置若缺失或过期就停止，不能盲用旧必经点。
2. 在私有目录准备本轮独立start/pause/stop/status路径。运行`mumu_route.py --distance 3100 --speed 3.3333333333 --start-signal <本轮start路径> --pause-signal <本轮pause路径> --stop-signal <本轮stop路径> --status-file <本轮status路径>`。等待armed，保持起点一次。
3. 运行独立Step输入并核验accepted持续增长、run_id和活跃instrumentation一致。例如后续建议预算：`step_sim.py --duration 1800 --cadence 180 --cadence-variation 6 --amplitude 6 --rate 50`。本轮实际用了旧1100秒段加续段，不能把例子当原始逐条命令。
4. 确认APP当前校园地图与五个点角色正确，人工GO，然后立即创建本轮start信号。读回APP步频和距离在增长，不能让APP已开始而GPS仍静止。
5. 人工暂停APP时同步创建pause文件；恢复后移除。若helper预算即将耗尽，在保留同一APP记录的前提下核验并续段；不能同时开两个注入者或重新GO。
6. 以APP实际3.00km和5/5等条件为准，停止GPS及本轮Step输入并确认NORMAL。人工在APP长按结束，查看成绩并保存。

## 已确认结果与限制

应用成绩达标，读回3.00km、18分40秒、178次/分钟、5/5；用户确认成功并完成结束保存。后来整理的新一键协调器未完整重放，所以本批明确排除。八项工程验收未全部完成，尤其不能将此结果直接推广为全部重启/重连/强杀故障或Mac平台通过。
