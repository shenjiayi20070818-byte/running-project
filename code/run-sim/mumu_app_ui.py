"""Read current Android UI via our permission-free observation instrumentation."""
import datetime
import json
import re
import socket
import time
import uuid

import lab

OBSERVER = 'com.codex.localuitree'


def capture(device):
    """No idle wait, target-app instrumentation, UI actions or permission changes."""
    name = 'tree-' + uuid.uuid4().hex + '.json'
    with socket.socket() as lock:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        lock.bind(('127.0.0.1', 15583))
        lock.listen(1)
        response = device.adb('shell', 'am', 'instrument', '-w', '-r', '-e', 'out',
                              name, OBSERVER + '/.TreeInstrumentation', timeout=15,
                              check=False)
        if not re.search(r'INSTRUMENTATION_RESULT: success=true\b', response):
            raise RuntimeError('本次只读UI采集未成功：' + response[:800])
        raw = device.adb('exec-out', 'run-as', OBSERVER, 'cat', 'files/' + name)
        tree = json.loads(raw)
        if not tree.get('success') or not tree.get('rootAvailable') or tree.get('truncated'):
            raise RuntimeError('本次UI树不完整，拒绝使用旧画面或猜测控件。')
        tree['_observed_monotonic'] = time.monotonic()
        tree['_host_captured_at'] = datetime.datetime.now().astimezone().isoformat()
        for node in tree.get('nodes', []):
            node['_observed_monotonic'] = tree['_observed_monotonic']
        return tree


def by_id(tree, suffix):
    found = [node for node in tree.get('nodes', [])
             if (node.get('resourceId') or '').endswith(':id/' + suffix)
             and node.get('visibleToUser')]
    if len(found) != 1:
        raise RuntimeError('当前可见UI中未唯一找到控件：' + suffix)
    return found[0]


def parse_metrics(tree):
    values = {(node.get('resourceId') or '').split('/')[-1]: node.get('text')
              for node in tree.get('nodes', []) if node.get('visibleToUser')}
    result = {'captured_at': tree.get('_host_captured_at'),
              'root_package': tree.get('rootPackage'),
              'paused': any('暂停剩余时间' in str(node.get('text') or '')
                            for node in tree.get('nodes', []) if node.get('visibleToUser'))}
    for name, ids in [('distance_km', ['tvValidDistance', 'tvValidDistance2', 'tvDistance']),
                      ('cadence_spm', ['tvStepAvg', 'tvStepAvg2'])]:
        for key in ids:
            text = values.get(key)
            if text is not None:
                try:
                    result[name] = float(text)
                except ValueError:
                    pass
                break
    pace = values.get('tvSpeed') or values.get('tvSpeed2')
    match = re.search(r'(\d+)\D+(\d+)', pace or '')
    result['pace_text'] = pace
    result['pace_s_per_km'] = int(match[1])*60 + int(match[2]) if match else None
    points = values.get('run_sequence_five_number')
    match = re.fullmatch(r'(\d+)/(\d+)', points or '')
    if match:
        result.update(points_passed=int(match[1]), points_total=int(match[2]))
    for key in ['tvValidTime', 'tvTime', 'tvDuration', 'tvUsedTime', 'tvUseTime']:
        if values.get(key):
            result['duration_text'] = values[key]
            break
    return result


def tap_node(device, node, hold_ms=0):
    """Operate only a fresh observed target-app node; never permissions/auth UI."""
    if time.monotonic() - node.get('_observed_monotonic', -100) > 5:
        raise RuntimeError('控件观察已过期，请重新采集。')
    if (node.get('packageName') != lab.APP or not node.get('visibleToUser')
            or not node.get('enabled')):
        raise RuntimeError('拒绝操作非目标应用或不可见的控件。')
    if re.search(r'权限|授权|隐私|登录|登陆|同意|允许|验证码|密码|支付',
                 str(node.get('text') or '') + str(node.get('contentDescription') or '')):
        raise RuntimeError('权限、隐私、登录或敏感操作由用户自行处理。')
    bounds = node['bounds']
    if bounds['right'] <= bounds['left'] or bounds['bottom'] <= bounds['top']:
        raise RuntimeError('控件没有有效屏幕范围。')
    x, y = (bounds['left'] + bounds['right'])//2, (bounds['top'] + bounds['bottom'])//2
    if hold_ms:
        if not 500 <= hold_ms <= 4000:
            raise ValueError('长按时长超出已支持范围。')
        return device.adb('shell', 'input', 'swipe', str(x), str(y), str(x), str(y), str(hold_ms))
    return device.adb('shell', 'input', 'tap', str(x), str(y))


if __name__ == '__main__':
    from mumu_runtime import Device
    device = Device(0)
    device.connect(timeout=10)
    current = capture(device)
    (lab.REPORTS/'app-ui-latest.json').write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(parse_metrics(current), ensure_ascii=False))
