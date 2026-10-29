"""Windows MuMu launcher for the verified 7.4.0 test environment."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import lab


def manager_path():
    roots = [os.environ.get('MUMU_HOME'),
             str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Netease/MuMu')]
    for root in roots:
        if root:
            for relative in ['nx_main/MuMuManager.exe', 'shell/MuMuManager.exe']:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return candidate
    raise RuntimeError('MuMuManager.exe not found; set MUMU_HOME to the official installation directory.')


def manager(*args):
    text = lab.run([manager_path(), *args], timeout=30)
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError('Unexpected MuMu manager response: ' + text[:1000])
    if data.get('errcode', 0):
        raise RuntimeError(text)
    return data


def connect(index, launch=True):
    info = manager('info', '-v', str(index))
    if not info.get('is_process_started'):
        if not launch:
            raise RuntimeError('The selected MuMu test device is not running.')
        manager('control', '-v', str(index), 'launch')
    deadline = time.monotonic() + 180
    print('Waiting for the MuMu Android test device...', flush=True)
    while time.monotonic() < deadline:
        info = manager('info', '-v', str(index))
        if info.get('launch_err_code'):
            raise RuntimeError(info.get('launch_err_msg', str(info)))
        if info.get('adb_host_ip') and info.get('adb_port'):
            host = info['adb_host_ip']
            if host not in ('127.0.0.1', 'localhost', '::1'):
                raise RuntimeError('Only a local MuMu ADB endpoint is accepted.')
            serial = f'{host}:{int(info["adb_port"])}'
            lab.run([lab.executable('adb'), 'connect', serial], timeout=10, check=False)
            result = lab.run([lab.executable('adb'), '-s', serial, 'shell',
                              'getprop', 'sys.boot_completed'], timeout=10, check=False)
            if result.strip() == '1':
                return serial
        time.sleep(3)
    raise RuntimeError('MuMu did not finish booting within 180 seconds.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'target', 'check', 'stop', 'status'])
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args()
    if args.index < 0:
        parser.error('--index must be nonnegative')
    if args.command == 'stop':
        print(json.dumps(manager('control', '-v', str(args.index), 'shutdown')))
        return 0
    if args.command == 'status':
        print(json.dumps(manager('info', '-v', str(args.index)), ensure_ascii=False, indent=2))
        return 0
    serial = connect(args.index)
    if args.command == 'start':
        from mumu_start_position import verify
        verify(args.index)
        print('MuMu ready: ' + serial)
        return 0
    command = [lab.executable('adb'), '-s', serial]
    package = lab.run([*command, 'shell', 'dumpsys', 'package', lab.APP], check=False)
    code = re.search(r'versionCode=(\d+)', package)
    version = re.search(r'versionName=([^\s]+)', package)
    if code and (not version or (code[1], version[1]) != ('408', '7.4.0')):
        raise RuntimeError('A different app version is installed. Use a separate MuMu test device for another version.')
    if not code:
        from inspect_apk import inspect
        apk = lab.BASE.parent / 'apks/sportsworld-7.4.0-408-baidu.apk'
        inspect(apk)
        print(lab.run([*command, 'install', str(apk)], timeout=180))
    from mumu_start_position import verify
    verify(args.index)
    if args.command == 'check':
        return subprocess.call([sys.executable, str(lab.BASE/'check_target.py'),
            '--serial', serial, '--launch', '--name', 'mumu-launch'], env=lab.env())
    print(lab.run([*command, 'shell', 'am', 'start', '-n',
                   lab.APP+'/'+lab.APP+'.mvi.splash.SplashActivity']))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('Error: ' + str(error))
        raise SystemExit(1)
