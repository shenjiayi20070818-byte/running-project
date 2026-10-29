"""Offline build/manifest/signature checks only; never invokes adb or a device."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from lab import BASE, WORK, sdk_root

PACKAGE = 'com.codex.localuitree'


def main():
    suffix = '.exe' if os.name == 'nt' else ''
    javac = next(iter((WORK / 'jdk').glob('*/bin/javac' + suffix)), None)
    if javac is None and os.environ.get('JAVA_HOME'):
        javac = Path(os.environ['JAVA_HOME']) / 'bin' / ('javac' + suffix)
    if javac is None:
        raise RuntimeError('JDK required; set JAVA_HOME')
    java = javac.with_name('java' + suffix)
    sdk = sdk_root()
    bt, android = sdk / 'build-tools/35.0.0', sdk / 'platforms/android-33/android.jar'
    source = BASE / 'ui-tree-helper'
    build = WORK / 'ui-tree-helper-build'
    classes, dex = build / 'classes', build / 'dex'
    classes.mkdir(parents=True, exist_ok=True)
    dex.mkdir(parents=True, exist_ok=True)
    ns = '{http://schemas.android.com/apk/res/android}'
    manifest = ET.parse(source / 'AndroidManifest.xml').getroot()
    assert manifest.attrib['package'] == PACKAGE
    assert not manifest.findall('uses-permission')
    application = manifest.find('application')
    assert application is not None and not list(application)
    assert manifest.find('instrumentation').attrib[ns + 'targetPackage'] == PACKAGE
    java_source = (source / 'TreeInstrumentation.java').read_text(encoding='utf-8')
    for forbidden in ['waitForIdle(', 'startActivity(', 'startActivitySync(', 'injectInputEvent(',
                      'performAction(', 'executeShellCommand(', 'adoptShellPermissionIdentity(',
                      'java.lang.reflect', 'java.net.', 'android.location.', 'android.hardware.']:
        assert forbidden not in java_source, forbidden

    def call(args):
        result = subprocess.run([str(x) for x in args], capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        text = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        print(text)
        result.check_returncode()
        return text

    call([javac, '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-bootclasspath', android,
          '-d', classes, *source.glob('*.java')])
    call([java, '-cp', bt / 'lib/d8.jar', 'com.android.tools.r8.D8', '--lib', android,
          '--min-api', '26', '--output', dex, *classes.rglob('*.class')])
    unsigned, aligned = build / 'unsigned.apk', build / 'aligned.apk'
    call([bt / ('aapt' + suffix), 'package', '-f', '-M', source / 'AndroidManifest.xml',
          '-I', android, '-F', unsigned])
    with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as package:
        package.write(dex / 'classes.dex', 'classes.dex')
    call([bt / ('zipalign' + suffix), '-f', '4', unsigned, aligned])
    key = WORK / 'sensor-injector-build/debug.keystore'
    if not key.is_file():
        raise RuntimeError('Expected existing local lab signing key; no replacement was generated')
    dest = BASE.parent / 'apks/mumu-ui-tree-observer.apk'
    dest.parent.mkdir(parents=True, exist_ok=True)
    call([java, '-jar', bt / 'lib/apksigner.jar', 'sign', '--ks', key, '--ks-pass', 'pass:android',
          '--key-pass', 'pass:android', '--out', dest, aligned])
    signature = call([java, '-jar', bt / 'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', dest])
    badging = call([bt / ('aapt' + suffix), 'dump', 'badging', dest])
    permissions = call([bt / ('aapt' + suffix), 'dump', 'permissions', dest])
    tree = call([bt / ('aapt' + suffix), 'dump', 'xmltree', dest, 'AndroidManifest.xml'])
    assert 'uses-permission' not in permissions
    assert 'launchable-activity:' not in badging
    assert 'E: activity ' not in tree and 'E: service ' not in tree
    assert PACKAGE in badging and PACKAGE in tree
    report = {
        'apk': str(dest.resolve()), 'sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
        'size_bytes': dest.stat().st_size, 'package': PACKAGE,
        'target_package': PACKAGE, 'uses_permissions': [], 'activities': [],
        'internet_permission': False, 'public_android_sdk_api': 33, 'min_api': 26,
        'device_commands_executed': False, 'runtime_validated': False,
        'signature_verification': signature, 'badging': badging,
        'permissions_dump': permissions, 'manifest_tree': tree,
        'limits': ['A standard UiAutomation connection is still needed at runtime',
                   'A null/stale root or custom-rendered view may not expose all screen text',
                   'UI-tree traversal is not an atomic frame; nodes may change mid-capture',
                   'No idle wait; Android accessibility node IPC may still take time',
                   'Concurrent automation ownership may conflict; no override is attempted'],
    }
    (build / 'artifact.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Built and checked offline:', dest)


if __name__ == '__main__':
    main()
