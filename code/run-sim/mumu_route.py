"""Continuously traverse the reviewed local MuMu route; optional file controls."""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import time

import lab
import mumu
from mumu_runtime import WriterLease, cli_main, save_json
from mumu_start_position import verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--distance',type=float,default=100)
    p.add_argument('--speed',type=float,default=3.3333333333)
    p.add_argument('--offset',type=float,default=0)
    p.add_argument('--index',type=int,default=0)
    p.add_argument('--start-signal',type=Path,help='Wait armed until this new file exists')
    p.add_argument('--stop-signal',type=Path,help='Stop without resetting position when this file exists')
    p.add_argument('--pause-signal',type=Path,help='Pause while this file exists; resume when removed')
    p.add_argument('--status-file','--status-json',dest='status_file',type=Path,
                   help='Atomic UTF-8 JSON status; defaults beside route evidence')
    a=p.parse_args()
    if not 0<a.distance<=4000 or not 1<=a.speed<=4.2 or a.offset<0:
        p.error('Unsupported distance, speed or route offset.')
    signal_paths=[x.resolve() for x in [a.start_signal,a.stop_signal,a.pause_signal,a.status_file] if x]
    if len(set(signal_paths))!=len(signal_paths):
        p.error('Start, stop, pause and status paths must be different.')
    if a.start_signal and a.start_signal.exists():
        raise RuntimeError('Start signal already exists; remove the stale file before arming a new route.')
    route=json.loads((lab.BASE/'run-route.json').read_text(encoding='utf-8'))
    pts=route['points'];center_lat=pts[0]['lat'];scale_lon=111320*math.cos(math.radians(center_lat))
    xy=[((x['lon']-pts[0]['lon'])*scale_lon,(x['lat']-center_lat)*111320) for x in pts]
    xy.append(xy[0]);lengths=[math.hypot(q[0]-r[0],q[1]-r[1]) for r,q in zip(xy,xy[1:])];lap=sum(lengths)
    if not lap>0:
        raise RuntimeError('The route has no positive length.')
    def position(distance):
        s=distance%lap
        for i,length in enumerate(lengths):
            if length<=0:continue
            if s<=length:
                f=s/length;x=xy[i][0]+(xy[i+1][0]-xy[i][0])*f;y=xy[i][1]+(xy[i+1][1]-xy[i][1])*f
                return center_lat+y/111320,pts[0]['lon']+x/scale_lon,i
            s-=length
        return pts[0]['lat'],pts[0]['lon'],0
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    path=lab.REPORTS/('mumu-route-'+stamp+'.jsonl')
    status_path=a.status_file or path.with_suffix('.status.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    state={'state':'initializing','pid':os.getpid(),'index':a.index,
        'distance_target_m':a.distance,'speed_m_s':a.speed,'offset_m':a.offset,
        'active_elapsed_s':0.0,'distance_commanded_m':0.0,'location_commands':0,
        'lat':None,'lon':None,'segment':None,'evidence':str(path.resolve()),
        'start_signal':str(a.start_signal.resolve()) if a.start_signal else None,
        'stop_signal':str(a.stop_signal.resolve()) if a.stop_signal else None,
        'pause_signal':str(a.pause_signal.resolve()) if a.pause_signal else None,
        'status_file':str(status_path.resolve()),'start_signal_received':a.start_signal is None}
    def publish(phase,**updates):
        state.update(updates);state['state']=phase
        state['updated_local']=datetime.datetime.now().astimezone().isoformat()
        save_json(status_path,state)
    def signaled(file):return file is not None and file.is_file()
    def stop_requested():return signaled(a.stop_signal)
    publish('initializing')
    if stop_requested():
        publish('stopped',reason='stop_signal_before_initialization')
        print('Stopped before location initialization. Status: '+str(status_path),flush=True)
        return
    if not mumu.manager('info','-v',str(a.index)).get('is_android_started'):
        publish('error',reason='test_device_not_running')
        raise RuntimeError('The test device is not running.')
    output=None
    try:
        with WriterLease('campus_route') as writer:
            verify(a.index, require_reviewed_route=True, writer_lease=writer)
            if stop_requested():
                publish('stopped',reason='stop_signal_before_first_point')
                return
            output=path.open('w',encoding='utf-8',newline='\n')
            output.write(json.dumps({'kind':'config','distance_m':a.distance,'speed_m_s':a.speed,
                'offset_m':a.offset,'lap_m':lap,'route':route,'controls':state},ensure_ascii=False)+'\n');output.flush()
            def send(elapsed):
                distance=min(a.distance,a.speed*elapsed)
                lat,lon,segment=position(a.offset+distance);before=time.monotonic()
                response=writer.location(a.index,lat,lon)
                row={'kind':'location','elapsed_s':elapsed,'distance_commanded_m':distance,
                    'lat':lat,'lon':lon,'segment':segment,'call_ms':round((time.monotonic()-before)*1000,2),
                    'response':response}
                output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush()
                state.update(active_elapsed_s=elapsed,distance_commanded_m=distance,lat=lat,lon=lon,
                             segment=segment,location_commands=state['location_commands']+1)
            # Exactly one first point before arming. The route timer has not started.
            send(0.0)
            publish('armed')
            print('Route armed. Status: '+str(status_path),flush=True)
            while a.start_signal and not signaled(a.start_signal):
                if stop_requested():
                    publish('stopped',reason='stop_signal_while_armed')
                    return
                time.sleep(.1)
            if stop_requested():
                publish('stopped',reason='stop_signal_before_start')
                return
            state['start_signal_received']=True
            base_elapsed=0.0;anchor=time.monotonic();due=anchor+1.0
            last_elapsed=0.0;last_progress=-10;paused=False
            publish('running')
            while True:
                # A stop never emits a compensating/reset/neutral GPS point. An
                # already in-flight synchronous CLI call must finish first.
                if stop_requested():
                    publish('stopped',reason='stop_signal')
                    break
                now=time.monotonic()
                if signaled(a.pause_signal):
                    if not paused:
                        paused=True;base_elapsed=last_elapsed
                        publish('paused')
                    time.sleep(.1)
                    continue
                if paused:
                    paused=False;anchor=now;due=now+1.0
                    publish('running',resumed=True)
                if now<due:
                    time.sleep(min(.1,due-now));continue
                if stop_requested():
                    publish('stopped',reason='stop_signal')
                    break
                elapsed=min(a.distance/a.speed,base_elapsed+now-anchor)
                send(elapsed);last_elapsed=elapsed
                if elapsed-last_progress>=10 or state['distance_commanded_m']>=a.distance:
                    print(f"elapsed={elapsed:.1f}s commanded={state['distance_commanded_m']:.1f}/{a.distance:.1f}m segment={state['segment']}",flush=True)
                    last_progress=elapsed
                if state['distance_commanded_m']>=a.distance:
                    publish('completed',reason='target_distance_reached')
                    break
                publish('running')
                # Skip overdue host ticks instead of queueing a burst. Pause resets
                # the anchor to the last emitted point, so there is no resume jump.
                due=anchor+(math.floor(time.monotonic()-anchor)+1)
    except KeyboardInterrupt:
        publish('stopped',reason='keyboard_interrupt')
    except BaseException as error:
        publish('error',reason=str(error))
        raise
    finally:
        if output is not None:output.close()
    print('Evidence: '+str(path)+'; status: '+str(status_path),flush=True)


if __name__=='__main__':
    raise SystemExit(cli_main(main))
