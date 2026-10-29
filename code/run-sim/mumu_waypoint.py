"""Apply a reviewed waypoint fixture without starting or submitting a run."""
import argparse
import json
import re
import subprocess
import sys

import lab
import mumu
from mumu_start_position import load_profile, route_reviewed


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--point', type=int, default=0)
    p.add_argument('--hold', type=float, default=20)
    p.add_argument('--allow-estimated', action='store_true')
    p.add_argument('--index', type=int, default=0)
    a=p.parse_args()
    data=json.loads((lab.BASE/'waypoints.json').read_text(encoding='utf-8'))
    if not 0<=a.point<len(data['points']) or not 1<=a.hold<=120:
        p.error('Unsupported point index or holding duration.')
    point=data['points'][a.point]
    profile=load_profile()
    if not route_reviewed(profile):
        raise RuntimeError('当前日期的地图布局尚未复核，拒绝前往旧必经点；--allow-estimated不能绕过此门禁。')
    if a.index!=profile['instance_index'] or a.point!=profile['waypoint_point_index'] or point.get('name')!=profile.get('waypoint_name'):
        raise RuntimeError('实例、点位索引或点名与已复核的启动配置不一致。')
    if point.get('lat') is None or point.get('lon') is None:
        raise RuntimeError('The waypoint coordinates have not been established.')
    if not data.get('verified') and not a.allow_estimated:
        raise RuntimeError('This is a visual map estimate. Review waypoints.json and pass --allow-estimated to apply it.')
    if data.get('coordinate_system')!='mumu_cli_observed_BD09':
        raise RuntimeError('Unsupported coordinate system for MuMu CLI input.')
    serial=mumu.connect(a.index,launch=False)
    package=lab.run([lab.executable('adb'),'-s',serial,'shell','dumpsys','package',lab.APP])
    if not re.search(r'versionCode=408\b',package) or not re.search(r'versionName=7\.4\.0\b',package):
        raise RuntimeError('The installed target is not version 7.4.0 / 408.')
    print('Applying waypoint fixture: '+str(a.point),flush=True)
    print('Estimated map error (m): '+str(point.get('estimated_error_m')),flush=True)
    print('This does not start a run or verify an application checkpoint.',flush=True)
    return subprocess.call([sys.executable,str(lab.BASE/'mumu_location.py'),
        '--scenario','stationary','--duration',str(a.hold),
        '--lat',str(point['lat']),'--lon',str(point['lon']),
        '--index',str(a.index)],env=lab.env())


if __name__=='__main__':
    raise SystemExit(main())
