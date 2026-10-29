# 01 GPS过程

成功环境使用MuMu Android实例和原始目标APP7.4.0/408。GPS输入通过MuMu公开CLI的`control -v <index> tool location -lat ... -lon ...`送入虚拟设备；它不是射频发射。封装见[WriterLease.location](../../code/run-sim/mumu_runtime.py#L154)，普通状态读取见`mumu_diagnose.py`。

[GPS初始化与实际读回](../../code/run-sim/mumu_fix_gnss.py#L56)先保持自建`GnssKeepAliveService`位置请求，再检查`dumpsys location`。卫星数、最新定位年龄、GPS请求状态分别验证；可见卫星/GNSS usedInFix和定位extras不是同一指标。系统定位关闭时停下由用户处理，不代开权限。

`position-profile.json`把MuMu CLI输入和对应Android原生GPS实测参考分开保存。两者不能直接用相同数字比较；本机曾观察到类似BD09转换，因此只保存独立参考与容差，不把推断宣称为官方坐标保证。公开模板不含这两组真实坐标或账号绑定，位置守卫会拒绝未配置输入。

[共享定位锁](../../code/run-sim/mumu_runtime.py#L83)使用本机15581防止多个写入者；CLI子进程继承锁，异常后等待在途命令的隔离期。这个机制保护输入一致性，不改变APP代码或GNSS硬件。

官方参考：[MuMu命令行](https://mumu.163.com/help/20240807/40912_1170006.html)、[Android GnssStatus](https://developer.android.com/reference/android/location/GnssStatus)。
