"""Send bounded location fixtures through MuMu's documented local CLI."""
import argparse
import datetime
import json
import math
import socket
import time

import lab
import mumu
from mumu_runtime import WriterLease


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario',choices=['stationary','moving'],default='stationary')
    p.add_argument('--duration',type=float,default=30)
    p.add_argument('--speed',type=float,default=3.3333333333333335)
    p.add_argument('--lat',type=float)
    p.add_argument('--lon',type=float)
    p.add_argument('--interval',type=float,default=1.0)
    p.add_argument('--index',type=int,default=0)
    a=p.parse_args()
    if (a.lat is None)!=(a.lon is None):
        p.error('--lat 与 --lon 必须同时提供。')
    using_saved_profile=a.lat is None
    if using_saved_profile:
        from mumu_start_position import load_profile,route_reviewed
        profile=load_profile()
        if not route_reviewed(profile):
            raise RuntimeError('保存点位尚未匹配当前日期的地图布局，拒绝使用旧默认坐标。请先复核 position-profile.json。')
        a.lat=float(profile['cli_input']['lat']);a.lon=float(profile['cli_input']['lon'])
    if not 0<a.duration<=3600 or not 0<=a.speed<=15 or not -85<=a.lat<=85 or not -180<=a.lon<=180 or not .5<=a.interval<=10:
        p.error('Invalid duration, speed, coordinate or interval.')
    info=mumu.manager('info','-v',str(a.index))
    if not info.get('is_android_started'):
        raise RuntimeError('Start the MuMu test device first.')
    with WriterLease('location_fixture') as writer:
        if using_saved_profile:
            from mumu_start_position import verify
            verify(a.index, require_reviewed_route=True, writer_lease=writer)
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        path=lab.REPORTS/('mumu-location-'+stamp+'.jsonl')
        started=time.monotonic();due=started;count=0
        with path.open('w',encoding='utf-8') as out:
            meta={'kind':'config','scenario':a.scenario,'duration_s':a.duration,'speed_m_s':a.speed,
                  'center_lat':a.lat,'center_lon':a.lon,'interval_s':a.interval,
                  'anchor_role':'saved_start_position' if using_saved_profile else 'explicit_fixture_center',
                  'coordinate_note':'MuMu CLI input coordinates; the observed conversion is consistent with BD09, not an official datum guarantee.'}
            out.write(json.dumps(meta,ensure_ascii=False)+'\n');out.flush()
            try:
                while time.monotonic()-started<a.duration:
                    time.sleep(max(0,due-time.monotonic()))
                    now=time.monotonic();elapsed=now-started
                    if elapsed>=a.duration:break
                    # An oval is a synthetic fixture; no target-app UI actions are performed.
                    x,y=lab.track(elapsed*a.speed) if a.scenario=='moving' else (0,0)
                    if using_saved_profile and a.scenario=='moving':
                        x0,y0=lab.track(0)
                        x-=x0;y-=y0
                    lat=a.lat+y/111320;lon=a.lon+x/(111320*math.cos(math.radians(a.lat)))
                    response=writer.location(a.index,lat,lon)
                    end=time.monotonic();count+=1
                    record={'kind':'location_command','elapsed_s':elapsed,'lat':lat,'lon':lon,
                            'duration_ms':round((end-now)*1000,3),'lateness_ms':round(max(0,now-due)*1000,3),
                            'response':response}
                    out.write(json.dumps(record,ensure_ascii=False)+'\n');out.flush()
                    due=started+(math.floor((end-started)/a.interval)+1)*a.interval
            except KeyboardInterrupt:
                print('Location fixture stopped.')
        print(f'Commands: {count}; evidence: {path}')


if __name__=='__main__':
    main()
