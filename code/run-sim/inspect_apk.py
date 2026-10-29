"""提取并保存 APK 版本、原生 ABI、签名和摘要；锁定本次测试版本。"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import zipfile
from lab import BASE,WORK,REPORTS,sdk_root,run

def inspect(path, strict=True):
    path=Path(path).resolve()
    bt=sdk_root()/'build-tools/35.0.0'
    raw=run([bt/('aapt.exe' if os.name=='nt' else 'aapt'),'dump','badging',path])
    line=next(x for x in raw.splitlines() if x.startswith('package:'))
    fields=dict(re.findall(r"([A-Za-z]+)='([^']*)'",line))
    data={'file':str(path),'size':path.stat().st_size,'package':fields['name'],
          'versionName':fields.get('versionName'),'versionCode':int(fields['versionCode'])}
    for alg in ['md5','sha256']:
        with path.open('rb') as f:data[alg]=hashlib.file_digest(f,alg).hexdigest()
    with zipfile.ZipFile(path) as z:
        libs=[x for x in z.namelist() if x.startswith('lib/') and x.endswith('.so')]
        data['native_abis']=sorted({x.split('/')[1] for x in libs})
        data['native_libraries']=libs
        # A directory name alone is insufficient: some protected APKs put x86
        # bootstrap libraries in ARM directories and choose them at runtime.
        machines={3:'x86',40:'ARM',62:'x86_64',183:'AArch64'}
        expected={'x86':3,'armeabi':40,'armeabi-v7a':40,'x86_64':62,'arm64-v8a':183}
        data['elf_machines_by_directory']={}
        data['elf_directory_mismatches']=[]
        for name in libs:
            with z.open(name) as stream:header=stream.read(20)
            if len(header)<20 or header[:4]!=b'\x7fELF':continue
            machine=int.from_bytes(header[18:20],'little' if header[5]==1 else 'big')
            abi=name.split('/')[1]
            counts=data['elf_machines_by_directory'].setdefault(abi,{})
            label=machines.get(machine,str(machine))
            counts[label]=counts.get(label,0)+1
            if abi in expected and machine!=expected[abi]:
                data['elf_directory_mismatches'].append({'file':name,'machine':label,'bits':64 if header[4]==2 else 32})
    java=next(iter((WORK/'jdk').glob('*/bin/java.exe')),None)
    if java is None and os.environ.get('JAVA_HOME'):
        java=Path(os.environ['JAVA_HOME'])/'bin'/('java.exe' if os.name=='nt' else 'java')
    if java is None:raise RuntimeError('签名核验需要 JDK，请设置 JAVA_HOME。')
    data['signature']=run([java,'-jar',bt/'lib/apksigner.jar','verify','--verbose','--print-certs',path])
    target=json.loads((BASE/'target.json').read_text(encoding='utf-8'))
    data['matches_target']=all(data[k]==target[k] for k in ['package','versionName','versionCode'])
    data['matches_baidu_file']=data['md5']==target['source_md5'] and data['size']==target['source_size']
    REPORTS.mkdir(parents=True,exist_ok=True)
    (REPORTS/(path.stem+'-inspection.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    if strict and not (data['matches_target'] and data['matches_baidu_file']):
        raise RuntimeError('APK 与锁定的百度 7.4.0 / 408 基线不一致，已停止安装。详情见 reports。')
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('apk');p.add_argument('--allow-other',action='store_true')
    a=p.parse_args()
    try:
        d=inspect(a.apk,not a.allow_other)
        print(json.dumps({k:v for k,v in d.items() if k!='native_libraries'},ensure_ascii=False,indent=2))
    except (RuntimeError,OSError) as e:print('错误：'+str(e));raise SystemExit(1)
