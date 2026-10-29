"""可复现的 Android GNSS/加速度实验。Python 3.10+，仅标准库。"""
from pathlib import Path
import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = Path(__file__).resolve().parent
WORK = BASE.parent.parent / 'work'
REPORTS = BASE.parent / 'reports'
SERIAL = 'emulator-5580'
PORT = 5580
PROBE = 'local.gnss.lab'
APP = 'com.zjwh.android_wh_physicalfitness'
AVD = 'GNSS_Research_API33'

def sdk_root():
    options = [os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
               str(WORK / 'android-sdk'), str(BASE / 'sdk'),
               str(Path.home() / 'AppData/Local/Android/Sdk'), str(Path.home() / 'Library/Android/sdk')]
    for p in options:
        if p and (Path(p) / 'platform-tools').exists(): return Path(p)
    raise RuntimeError('未找到 Android SDK；请设置 ANDROID_HOME，或把 SDK 放到 sdk 目录。')

def env():
    e = os.environ.copy()
    root = sdk_root()
    e.update(ANDROID_HOME=str(root), ANDROID_SDK_ROOT=str(root),
             ANDROID_USER_HOME=str(WORK / 'android-user'),
             ANDROID_EMULATOR_HOME=str(WORK / 'android-user'),
             ANDROID_AVD_HOME=str(WORK / 'avd'), PYTHONIOENCODING='utf-8')
    for key in ['ANDROID_USER_HOME','ANDROID_AVD_HOME']:
        Path(e[key]).mkdir(parents=True, exist_ok=True)
    return e

def executable(name):
    directory = 'emulator' if name == 'emulator' else 'platform-tools'
    return str(sdk_root() / directory / (name + ('.exe' if os.name == 'nt' else '')))

def run(args, timeout=30, check=True, binary=False):
    p = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout,
                       env=env(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if check and p.returncode:
        raise RuntimeError((p.stdout+p.stderr).decode('utf-8', errors='replace').strip())
    return p.stdout if binary else (p.stdout+p.stderr).decode('utf-8', errors='replace').strip()

def adb(*args, timeout=30, check=True, binary=False):
    return run([executable('adb'),'-s',SERIAL,*args],timeout,check,binary)

def wait_boot(timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            if adb('shell','getprop','sys.boot_completed',timeout=5)=='1': return
        except (RuntimeError,subprocess.TimeoutExpired): pass
        time.sleep(2)
    raise RuntimeError(f'模拟器未在 {timeout} 秒内启动，请查看启动日志。')

class Console:
    def __init__(self):
        self.s=socket.create_connection(('127.0.0.1',PORT),timeout=3)
        self.s.settimeout(3); self.buffer=b''
        greeting=self.reply()
        if 'Authentication required' in greeting:
            candidates=[WORK/'android-user/.emulator_console_auth_token',
                        Path.home()/'.emulator_console_auth_token',
                        Path.home()/'.android/.emulator_console_auth_token']
            # The emulator tells us the actual token file in its greeting.
            for line in greeting.splitlines():
                line=line.strip().strip("'")
                if '.emulator_console_auth_token' in line and Path(line).is_file():
                    candidates.insert(0,Path(line))
            token=next((p.read_text().strip() for p in candidates if p.is_file()),None)
            if not token: raise RuntimeError('找不到模拟器控制台认证文件。')
            self.command('auth '+token)
    def reply(self):
        lines=[]
        while True:
            while b'\n' not in self.buffer:
                chunk=self.s.recv(65536)
                if not chunk: raise ConnectionError('模拟器控制台断开')
                self.buffer+=chunk
            line,self.buffer=self.buffer.split(b'\n',1)
            line=line.decode('utf-8',errors='replace').strip();lines.append(line)
            if line=='OK': return '\n'.join(lines)
            if line.startswith('KO:'): raise RuntimeError(line)
    def command(self,value):
        self.s.sendall((value+'\n').encode('ascii'))
        return self.reply()
    def close(self): self.s.close()

def diagnose():
    REPORTS.mkdir(exist_ok=True,parents=True)
    data={'host':platform.platform(),'architecture':platform.machine(),'sdk':str(sdk_root())}
    for key,command in [('adb',[executable('adb'),'version']),('emulator',[executable('emulator'),'-version']),
                        ('acceleration',[executable('emulator'),'-accel-check']),('devices',[executable('adb'),'devices','-l'])]:
        data[key]=run(command,check=False)
    if SERIAL in data['devices']:
        data['api']=adb('shell','getprop','ro.build.version.sdk')
        data['supported_abis']=adb('shell','getprop','ro.product.cpu.abilist')
        data['native_bridge']=adb('shell','getprop','ro.dalvik.vm.native.bridge')
        for key,args in [('build',['getprop']),('location',['dumpsys','location']),
                         ('sensors',['dumpsys','sensorservice']),('graphics',['dumpsys','SurfaceFlinger'])]:
            data[key]=adb('shell',*args,check=False)
        data['gnss_hash']=adb('shell','sha256sum','/vendor/bin/hw/android.hardware.gnss@2.0-service.ranchu',check=False)
    target=REPORTS/'environment.json'
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('环境报告：'+str(target))
    print(data['acceleration']);print(data['devices'])
    if 'build' not in data: print('目标模拟器尚未运行；启动后可获取 GNSS、传感器和 GLES 信息。')
    return data

def start(args):
    if SERIAL in run([executable('adb'),'devices']):
        wait_boot()
        current_api=adb('shell','getprop','ro.build.version.sdk')
        if current_api!=str(args.api):
            raise RuntimeError(f'当前运行的是 API {current_api}；请先 stop，再启动 API {args.api}。')
        print('实验模拟器已在线。');return
    abi='arm64-v8a' if platform.machine().lower() in ('arm64','aarch64') else 'x86_64'
    avd_name=f'GNSS_Research_API{args.api}'
    image=sdk_root()/f'system-images/android-{args.api}/google_apis'/abi
    if not (image/'system.img').exists(): raise RuntimeError('缺少匹配宿主架构的镜像：'+str(image))
    avd=WORK/'avd'/(avd_name+'.avd');avd.mkdir(parents=True,exist_ok=True)
    config={ 'avd.ini.encoding':'UTF-8','AvdId':avd_name,'avd.ini.displayname':f'GNSS Research API {args.api}',
        'abi.type':abi,'hw.cpu.arch':'arm64' if abi=='arm64-v8a' else 'x86_64','hw.cpu.ncore':'2',
        'hw.ramSize':'2048','hw.lcd.width':'720','hw.lcd.height':'1560','hw.lcd.density':'294',
        'hw.gpu.enabled':'yes','hw.gpu.mode':args.gpu,'hw.gps':'yes','hw.accelerometer':'yes',
        'hw.audioInput':'no','hw.audioOutput':'no','hw.camera.back':'none','hw.camera.front':'none',
        'hw.keyboard':'yes','disk.dataPartition.size':'6G','image.sysdir.1':str(image)+os.sep,
        'tag.id':'google_apis','tag.display':'Google APIs','PlayStore.enabled':'false',
        'fastboot.forceColdBoot':'yes','showDeviceFrame':'no'}
    (avd/'config.ini').write_text('\n'.join(k+'='+v for k,v in config.items())+'\n',encoding='utf-8')
    (avd.parent/(avd_name+'.ini')).write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-{args.api}\n',encoding='utf-8')
    REPORTS.mkdir(parents=True,exist_ok=True)
    command=[executable('emulator'),'-avd',avd_name,'-port',str(PORT),'-no-snapshot','-no-boot-anim',
             '-memory','2048','-cores','2','-gpu',args.gpu,'-feature','-Vulkan','-no-audio']
    if not args.show: command+=['-no-window']
    with (REPORTS/'emulator-start.log').open('wb') as log:
        subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env(),
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    print('正在启动安卓实验环境……',flush=True);wait_boot()
    print('模拟器已启动，兼容图形模式：'+args.gpu)

def track(s,r=37.0):
    half=42.195;straight=2*half;curve=math.pi*r;s%=2*straight+2*curve
    if s<straight:return -r,-half+s
    s-=straight
    if s<curve:
        a=math.pi-s/r;return r*math.cos(a),half+r*math.sin(a)
    s-=curve
    if s<straight:return r,half-s
    a=-(s-straight)/r;return r*math.cos(a),-half+r*math.sin(a)

def simulate(args):
    if args.lat is None or args.lon is None:
        raise RuntimeError("Sanitized export: explicit reviewed test coordinates are required; no default location is supplied.")
    if not 0<=args.speed<=15 or not 0<=args.cadence<=240: raise RuntimeError('速度范围 0~15 m/s，步频范围 0~240。')
    if not -85<=args.lat<=85 or not -180<=args.lon<=180: raise RuntimeError('经纬度超出实验范围。')
    if args.duration<=0: raise RuntimeError('实验时长必须大于 0。')
    wait_boot()
    # Exclusive local listener prevents a second injector, with no PID reuse hazard.
    lock=socket.socket()
    if os.name=='nt':lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:lock.bind(('127.0.0.1',15580));lock.listen(1)
    except OSError:raise RuntimeError('已有模拟程序运行，请先关闭它，避免多个坐标源相互覆盖。')
    REPORTS.mkdir(exist_ok=True,parents=True)
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output=REPORTS/('injection-'+stamp+'.jsonl')
    started=time.monotonic();next_sensor=started;next_gps=started;last_log=started
    sent_gps=0;sent_sensor=0;reconnects=0;c=None
    print('开始可复现实验：'+args.scenario+'；Ctrl+C 停止。',flush=True)
    try:
        with output.open('w',encoding='utf-8') as f:
            f.write(json.dumps({'kind':'config',**vars(args)},ensure_ascii=False)+'\n')
            while (elapsed:=time.monotonic()-started)<args.duration:
                if c is None:
                    try:c=Console()
                    except (OSError,RuntimeError):reconnects+=1;time.sleep(2);continue
                now=time.monotonic()
                try:
                    if now>=next_gps and not (args.scenario=='once' and sent_gps):
                        x,y=track(elapsed*args.speed if args.scenario=='moving' else 0)
                        lat=args.lat+y/111320.;lon=args.lon+x/(111320.*math.cos(math.radians(args.lat)))
                        knots=args.speed/0.514444 if args.scenario=='moving' else 0
                        c.command(f'geo fix {lon:.8f} {lat:.8f} 10 {args.satellites} {knots:.5f}')
                        f.write(json.dumps({'kind':'geo','t':elapsed,'lat':lat,'lon':lon})+'\n')
                        sent_gps+=1;next_gps=now+1
                    if now>=next_sensor:
                        ph=2*math.pi*args.cadence/60*elapsed
                        x=0.9*math.sin(ph+math.pi/2) if args.cadence else 0
                        y=0.5*math.sin(ph*0.5) if args.cadence else 0
                        z=9.8+3.2*math.sin(ph)+math.sin(2*ph) if args.cadence else 9.8
                        c.command(f'sensor set acceleration {x:.4f}:{y:.4f}:{z:.4f}')
                        sent_sensor+=1;next_sensor+=0.02
                        if next_sensor<time.monotonic()-0.02:next_sensor=time.monotonic()+0.02
                    if now-last_log>=10:
                        print(f'已运行 {elapsed:.0f} 秒；定位 {sent_gps} 条；加速度 {sent_sensor} 帧；重连 {reconnects}',flush=True)
                        last_log=now;f.flush()
                except (OSError,ConnectionError):
                    c.close();c=None;reconnects+=1;time.sleep(2)
                time.sleep(max(0,min(next_sensor-time.monotonic(),0.01)))
    finally:
        if c:
            with contextlib.suppress(Exception):c.command('sensor set acceleration 0:0:9.8')
            c.close()
        lock.close()
        print('注入日志：'+str(output))

def capture(args):
    REPORTS.mkdir(exist_ok=True,parents=True)
    data=adb('exec-out','run-as',PROBE,'cat','files/events.jsonl',binary=True)
    target=REPORTS/(args.name+'.jsonl');target.write_bytes(data)
    events=[json.loads(x) for x in data.decode('utf-8').splitlines() if x.strip()]
    fixes=[x for x in events if x['kind']=='location'];gnss=[x for x in events if x['kind']=='gnss']
    sensor=[x for x in events if x['kind']=='sensor' and x['type']==1]
    summary={'location_events':len(fixes),'gnss_events':len(gnss),'acceleration_events':len(sensor),
             'visible_max':max((x['visible'] for x in gnss),default=None),'used_max':max((x['used'] for x in gnss),default=None),
             'distinct_locations':len({(x['lat'],x['lon']) for x in fixes}),
             'location_elapsed_ns_monotonic':all(b['locationElapsedNs']>a['locationElapsedNs'] for a,b in zip(fixes,fixes[1:]))}
    target.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('采集文件：'+str(target))

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    s.add_parser('diagnose');s.add_parser('stop');s.add_parser('probe');s.add_parser('reset-probe')
    a=s.add_parser('start');a.add_argument('--gpu',default='swangle');a.add_argument('--show',action='store_true')
    a.add_argument('--api',type=int,choices=[30,33],default=30,
                   help='30: Windows ARM 转译兼容实验；33: 原 GNSS 基线镜像')
    a=s.add_parser('install');a.add_argument('apk',type=Path)
    a=s.add_parser('capture');a.add_argument('--name',default='probe-capture')
    a=s.add_parser('simulate');a.add_argument('--scenario',choices=['once','stationary','moving'],default='moving')
    a.add_argument('--duration',type=float,default=60);a.add_argument('--speed',type=float,default=2.6)
    a.add_argument('--cadence',type=float,default=168);a.add_argument('--lat',type=float,default=None)
    a.add_argument('--lon',type=float,default=None);a.add_argument('--satellites',type=int,choices=range(1,13),default=12)
    args=p.parse_args()
    if args.command=='diagnose':diagnose()
    elif args.command=='start':start(args)
    elif args.command=='install':
        from inspect_apk import inspect
        probe=(BASE.parent/'apks/gnss-observer.apk').resolve()
        metadata=None if args.apk.resolve()==probe else inspect(args.apk)
        wait_boot()
        if metadata and metadata['native_abis']:
            supported=set(adb('shell','getprop','ro.product.cpu.abilist').split(','))
            if not supported.intersection(metadata['native_abis']):
                raise RuntimeError('模拟器 ABI 不兼容此 APK；请使用带 ARM 转译的 API 30 镜像。')
        print(adb('install','-r','-g',str(args.apk.resolve()),timeout=180))
    elif args.command in ('probe','reset-probe'):
        wait_boot();adb('shell','am','force-stop',PROBE)
        if args.command=='reset-probe':adb('shell','run-as',PROBE,'rm','-f','files/events.jsonl')
        print(adb('shell','am','start','-n',PROBE+'/.MainActivity'))
    elif args.command=='simulate':simulate(args)
    elif args.command=='capture':capture(args)
    elif args.command=='stop':print(adb('emu','kill',check=False))

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已停止。')
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as e:print('错误：'+str(e));sys.exit(1)
