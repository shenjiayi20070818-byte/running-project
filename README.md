# 成功分步流程归档：GPS、轨迹、步频与里程读回

这是已成功会话的 **MuMu分步流程去敏源码快照**，包含后来修复的主机监测、信号控制和恢复逻辑。它不是对当时每一个文件的逐字节版本锁定；manifest记录具体来源与SHA映射。新全自动协调器没有整轮重放，因此本批不发布该协调器或其启动器。

## 分类入口

- [GPS过程](docs/implementation/01-gps.md)
- [轨迹模拟](docs/implementation/02-trajectory.md)
- [步频模拟](docs/implementation/03-cadence.md)
- [里程累计与应用读回](docs/implementation/04-distance.md)
- [成功运行过程与复现步骤](docs/usage/successful-run.md)
- [匿名参数模板](templates/successful-run.template.json)
- [官方理论来源](docs/theory/README.md)
- [验证边界](docs/validation/README.md)
- [核心自建源码](code/run-sim/) · [SHA来源映射](manifest.json) · [隐私扫描](privacy-scan.json)

应用结果为3.00km、18分40秒、平均步频178、点位5/5、成绩达标，用户确认运行成功并手工结束保存。输入器累计3100米不能当作APP累计源码或服务端算法；APP内部里程算法未知。

本包不含APK、真实坐标、账号绑定、轨迹日志、截图、用户附件、签名密钥或凭据。配置为null且未review，禁止默认运行。先在仓库外私有目录恢复并审核本机ZIP配置及依赖。不要把私有配置、完整ZIP或原始证据回传公共仓库。

`lab.py`只保留作构建/ADB/常量依赖；它的旧Android SDK模拟路径不是本轮成功路径。MuMu圆形几何函数作为位置守卫依赖保留，也不表示本轮使用圆轨迹。未发布自动GO/自动结束/自动保存；本批使用人工APP操作配合输入器信号。
