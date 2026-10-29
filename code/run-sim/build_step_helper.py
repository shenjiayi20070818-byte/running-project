"""Build the independently signed narrow Step Lab platform-test helper; no device changes."""
from pathlib import Path
import os
import subprocess
import zipfile
from lab import BASE, WORK, sdk_root


def main():
    suffix = '.exe' if os.name == 'nt' else ''
    javac = next(iter((WORK/'jdk').glob('*/bin/javac' + suffix)), None)
    if javac is None and os.environ.get('JAVA_HOME'):
        javac = Path(os.environ['JAVA_HOME'])/'bin'/('javac' + suffix)
    if javac is None:
        raise RuntimeError('JDK required; set JAVA_HOME')
    java, keytool = javac.with_name('java' + suffix), javac.with_name('keytool' + suffix)
    source = BASE/'step-helper'
    build = WORK/'step-helper-build'
    classes, dex = build/'classes', build/'dex'
    classes.mkdir(parents=True, exist_ok=True)
    dex.mkdir(parents=True, exist_ok=True)
    sdk = sdk_root()
    bt, android = sdk/'build-tools/35.0.0', sdk/'platforms/android-33/android.jar'

    def call(args):
        result = subprocess.run([str(x) for x in args], capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        print((result.stdout + result.stderr).decode('utf-8', errors='replace'))
        result.check_returncode()

    call([javac, '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-bootclasspath', android,
          '-d', classes, *source.glob('*.java')])
    call([java, '-cp', bt/'lib/d8.jar', 'com.android.tools.r8.D8', '--lib', android,
          '--min-api', '26', '--output', dex, *classes.rglob('*.class')])
    unsigned, aligned = build/'unsigned.apk', build/'aligned.apk'
    call([bt/('aapt' + suffix), 'package', '-f', '-M', source/'AndroidManifest.xml',
          '-I', android, '-F', unsigned])
    with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as package:
        package.write(dex/'classes.dex', 'classes.dex')
    call([bt/('zipalign' + suffix), '-f', '4', unsigned, aligned])
    key = WORK/'sensor-injector-build/debug.keystore'
    if not key.exists():
        call([keytool, '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
              '-alias', 'androiddebugkey', '-dname', 'CN=Local MuMu Sensor Self Test',
              '-keyalg', 'RSA', '-validity', '3650'])
    dest = BASE.parent/'apks/mumu-step-test.apk'
    call([java, '-jar', bt/'lib/apksigner.jar', 'sign', '--ks', key, '--ks-pass', 'pass:android',
          '--key-pass', 'pass:android', '--out', dest, aligned])
    call([java, '-jar', bt/'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', dest])
    call([bt/('aapt' + suffix), 'dump', 'badging', dest])
    print('Built: ' + str(dest))


if __name__ == '__main__':
    main()
