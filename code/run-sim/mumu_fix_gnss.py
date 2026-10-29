"""通过自建前台观测服务和 MuMu 定位接口初始化 GPS；重复运行可安全跳过写入。"""
import argparse
import datetime
import json
import sys
import time

import lab
from mumu_runtime import Device, WriterLease, cli_main, read_gps, save_json

HELPER = lab.APP + '.sensorlab'
COMPONENT = HELPER + '/.GnssKeepAliveService'
PROGRESS = 'files/gnss-observer-progress.json'


def observer_progress(device):
    raw = device.adb('shell', 'run-as', HELPER, 'cat', PROGRESS, check=False)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def healthy(gps, max_age=5):
    satellites = gps.get('satellites_in_fix_extras')
    age = gps.get('last_fix_age_s')
    return satellites is not None and satellites > 0 and age is not None and age <= max_age and gps.get('gps_started')


def ensure_observer(device):
    # A service without an intent-filter need not appear in dumpsys's resolver
    # tables. Query the explicit component instead of searching those tables.
    component = device.adb('shell', 'cmd', 'package', 'query-services', '--components',
                           '--user', '0', '-n', COMPONENT, check=False)
    if 'GnssKeepAliveService' not in component:
        raise RuntimeError('自建 GNSS 前台观测服务尚未安装。先构建并安装包含 GnssKeepAliveService 的 mumu-sensor-test.apk。')
    result = device.adb('shell', 'am', 'start-foreground-service', '-n', COMPONENT,
                        '--el', 'interval_ms', '1000', check=False)
    if 'Error' in result or 'Exception' in result:
        raise RuntimeError('GNSS 前台观测服务启动失败：' + result)
    return result


def input_coordinate(index, lat, lon):
    if lat is not None:
        return lat, lon, 'explicit_cli_coordinate'
    from mumu_start_position import load_profile, route_reviewed
    profile = load_profile()
    if index != profile['instance_index'] or not route_reviewed(profile):
        raise RuntimeError('当前日期的启动配置尚未审核，禁止回落到旧坐标或任意上次轨迹点。')
    point = profile['cli_input']
    return float(point['lat']), float(point['lon']), 'reviewed position-profile.json'


def fix(index=0, lat=None, lon=None, timeout=20, max_age=5):
    device = Device(index)
    info = device.connect(timeout=120)
    before, before_text = read_gps(device)
    if not before.get('gps_enabled'):
        raise RuntimeError('系统定位已关闭，请由你在Android设置中开启；本工具不会代办定位或权限设置。')
    observer_started = ensure_observer(device)
    start = time.monotonic()
    after, after_text = before, before_text
    wrote = False
    selected_input = None
    # Activating a request often makes the native MuMu GNSS provider start
    # immediately. Give it a short window before issuing any location command.
    while time.monotonic()-start < min(timeout, 4):
        after, after_text = read_gps(device)
        if healthy(after, max_age):
            break
        time.sleep(.5)
    if not healthy(after, max_age):
        selected_lat, selected_lon, source = input_coordinate(index, lat, lon)
        selected_input = {'lat': selected_lat, 'lon': selected_lon, 'source': source}
        print('初始化定位坐标来源：' + source, flush=True)
        # The shared lock rejects overlap with circle/route writers. No satellite
        # property spoof, HAL patch, test-provider replacement or APK change.
        with WriterLease('fix_gnss') as writer:
            from mumu_start_position import active_run
            if active_run(device):
                raise RuntimeError('检测到活动RunRandomActivity，拒绝修复工具跳点；显式坐标也不能覆盖进行中的会话。')
            writer.location(index, selected_lat, selected_lon)
        wrote = True
        while time.monotonic()-start < timeout:
            after, after_text = read_gps(device)
            if healthy(after, max_age):
                break
            time.sleep(.5)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    prefix = lab.REPORTS / ('mumu-fix-gnss-' + stamp)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_name(prefix.name+'.before.txt').write_text(before_text, encoding='utf-8')
    prefix.with_name(prefix.name+'.after.txt').write_text(after_text, encoding='utf-8')
    result = {'created_local': datetime.datetime.now().astimezone().isoformat(),
        'index': index, 'serial': device.serial, 'mumu_info': info,
        'before': before, 'after': after, 'observer_component': COMPONENT,
        'observer_start_response': observer_started, 'observer_progress': observer_progress(device),
        'location_command_sent': wrote, 'input': selected_input,
        'passed': healthy(after, max_age), 'passed_gnss': healthy(after, max_age),
        'position_checked': False, 'maximum_fix_age_s': max_age,
        'operation': 'official MuMu location API and own foreground location requester'}
    save_json(prefix.with_suffix('.json'), result)
    save_json(lab.REPORTS / 'mumu-fix-gnss-latest.json', result)
    print('GPS 卫星数：' + str(after['satellites_in_fix_extras']) + '；最近定位年龄：' + str(after['last_fix_age_s']) + ' 秒。')
    print('本次发送定位命令：' + str(wrote) + '；验收通过：' + str(result['passed']))
    print('证据：' + str(prefix.with_suffix('.json')))
    if not result['passed']:
        raise RuntimeError('尚未确认 GPS 正在更新且 satellites>0；已保存原始状态，不会伪造通过。')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--lat', type=float)
    parser.add_argument('--lon', type=float)
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--max-age', type=float, default=5)
    args = parser.parse_args()
    if (args.lat is None) != (args.lon is None):
        parser.error('--lat 和 --lon 必须同时提供。')
    if not 5 <= args.timeout <= 120 or not 1 <= args.max_age <= 30:
        parser.error('timeout 应为 5~120 秒，max-age 应为 1~30 秒。')
    if args.lat is not None and not (-85 <= args.lat <= 85 and -180 <= args.lon <= 180):
        parser.error('坐标超出实验范围。')
    fix(args.index, args.lat, args.lon, args.timeout, args.max_age)


if __name__ == '__main__':
    sys.exit(cli_main(main))
