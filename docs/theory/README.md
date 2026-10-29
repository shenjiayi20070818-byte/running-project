# 理论基础

以下为官方资料核验后的简述，核验日期2026-10-29。这里区分协议/API定义、模拟输入与应用自身规则。

## GPS/GNSS与信号质量

GPS接收机比较卫星发送和本机接收时间，结合卫星位置计算距离及接收机位置。通常需要至少四颗卫星共同求解三维位置和时钟偏差；应用显示“信号差”还可能采用自己的卫星、时间或定位条件，不能仅凭显示推断其算法。[FAA GPS原理](https://www.faa.gov/about/office_org/headquarters_offices/ato/service_units/techops/navservices/gnss/gps/howitworks)

Android `GnssStatus.getSatelliteCount`、`getCn0DbHz`和`usedInFix`是不同指标；参与最近定位与可见数量不是同义词。定位回调时间的新鲜度还需单独观察。[Android GnssStatus](https://developer.android.com/reference/android/location/GnssStatus)

## 模拟器注入与更新

Android Emulator控制台支持`geo fix`、NMEA和传感器事件；这些是虚拟环境的数据输入，不是在空气中发射GPS射频。文档说明的模拟位置提供者将位置送入Android定位接口。不能把某个镜像的卫星行为或静止回调规律推广到全部模拟器。[Emulator控制台](https://developer.android.com/studio/run/emulator-console)

## ISA、ABI与原生库

位数相同不表示指令集、系统API或ABI相同。Android支持的ARM与x86 ABI具有不同指令和调用规则，库通常按`lib/<abi>/`目录放置。[Android ABI](https://developer.android.com/ndk/guides/abis)

官方Android11模拟器镜像提供过ARM二进制执行能力，但不能据此保证所有加固加载器兼容。[Android官方ARM兼容说明](https://android-developers.googleblog.com/2020/03/run-arm-apps-on-android-emulator.html)

AOSP Android11的NativeLoaderNamespace按原生/转译命名空间选择加载接口。混合架构库的错误应根据真实ELF类型、所选进程ABI与链接日志判断，不能通过改内存或显卡模式推断已修复。[AOSP源码](https://android.googlesource.com/platform/art/+/refs/heads/android11-release/libnativeloader/native_loader_namespace.cpp)

## 加速度、步频与计步

Android区分加速度、线性加速度、步数计数和步进等传感器；设备支持情况要枚举检查。加速度读数通常包含重力，软件可以在周期信号中识别峰值，但模拟波形不能证明存在硬件步数器，也不能证明目标APP使用同一算法。[Android运动传感器](https://developer.android.com/develop/sensors-and-location/sensors/sensors_motion)

本实验的周期波形是合成测试夹具。例如2.8Hz每周期计一个峰值，对应168次/分钟；是否形成APP步频，必须读回APP。轨迹速度应由相邻点距离除时间验证，同时区分计划点、API接受、设备回调和APP累计距离。

## 运行协调原则

同一时刻只允许一个定位写入者；启动前先armed，再确认传感器accepted，之后GO。暂停冻结模拟时钟，断线不把离线时间算作有效运动。停止输入与结束保存记录是不同操作。以上是本工具的工程设计，不是对第三方应用内部算法的推断。
