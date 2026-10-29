"""读取当前 MuMu 实例的路径、GPS 卫星、图形与计步传感器证据。"""
import argparse
import datetime
import json
from pathlib import Path
import re
import sys

import lab
import mumu
from mumu_runtime import Device, cli_main, read_gps, save_json


def collect(index=0):
    device = Device(index)
    info = device.connect(timeout=20)
    gps, location = read_gps(device)
    sensors = device.adb('shell', 'dumpsys', 'sensorservice')
    graphics = device.adb('shell', 'dumpsys', 'SurfaceFlinger')
    props = device.adb('shell', 'getprop')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    prefix = lab.REPORTS / ('mumu-diagnose-' + stamp)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix, text in [('location', location), ('sensors', sensors), ('graphics', graphics), ('properties', props)]:
        prefix.with_name(prefix.name + '.' + suffix + '.txt').write_text(text, encoding='utf-8')
    inventory_lines = [line.strip() for line in sensors.splitlines()
        if re.search(r'android\.sensor\.step_(?:counter|detector)|step[ _-]?(?:counter|detector)', line, re.I)]
    control_path = mumu.manager_path().resolve()
    application_path = next((path for path in [control_path.parent/'MuMuNxMain.exe',
        control_path.parent/'MuMuPlayer.exe'] if path.is_file()), None)
    data = {'created_local': datetime.datetime.now().astimezone().isoformat(),
        'runtime': 'MuMu', 'index': index, 'adb_serial': device.serial,
        'adb_executable': str(Path(lab.executable('adb')).resolve()),
        'emulator_control_executable': str(control_path),
        'emulator_application_executable': str(application_path) if application_path else None,
        'sdk_emulator_executable_if_installed': str(Path(lab.executable('emulator')).resolve()),
        'sdk_emulator_is_active_runtime': False, 'mumu_info': info,
        'gps': gps, 'gles_lines': [line.strip() for line in graphics.splitlines() if 'GLES:' in line],
        'step_sensor_evidence': inventory_lines,
        'step_counter_listed': bool(re.search(r'android\.sensor\.step_counter|step[ _-]?counter', '\n'.join(inventory_lines), re.I)),
        'step_detector_listed': bool(re.search(r'android\.sensor\.step_detector|step[ _-]?detector', '\n'.join(inventory_lines), re.I)),
        'step_sensor_note': '传感器清单表示系统接口；不能据此认定存在真实计步硬件。无匹配时查阅原始 sensorservice 文本。',
        'raw_report_prefix': str(prefix)}
    target = prefix.with_suffix('.json')
    save_json(target, data)
    save_json(lab.REPORTS / 'mumu-diagnose-latest.json', data)
    print('当前运行环境：MuMu；ADB：' + device.serial)
    print('ADB 路径：' + data['adb_executable'])
    print('模拟器控制程序：' + data['emulator_control_executable'])
    print('模拟器应用程序：' + str(data['emulator_application_executable']))
    print('GPS 卫星数（最近定位 extras）：' + str(gps['satellites_in_fix_extras']))
    print('最近定位年龄（秒）：' + str(gps['last_fix_age_s']))
    print('GLES：' + (' | '.join(data['gles_lines']) or '未从 SurfaceFlinger 提取到，见原始报告'))
    print('计步器接口：' + str(data['step_counter_listed']) + '；步进接口：' + str(data['step_detector_listed']))
    print('报告：' + str(target))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args()
    collect(args.index)


if __name__ == '__main__':
    sys.exit(cli_main(main))
