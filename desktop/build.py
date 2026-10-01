#!/usr/bin/env python3
"""Build the portable protocol core without an Android SDK.

Only explicitly selected upstream files are compiled. The generated directory is
throw-away: Android logging is redirected, no protocol implementation is copied
into a second maintained source tree. A newly introduced Android dependency fails
the build instead of being silently stubbed. Compiler memory is independent of
the receiver runtime's 256 MB JVM heap cap; build on a development host when possible.
"""
from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT.parent / 'shared/src/main/java/com/shilapi/xcertplay'
GENERATED = ROOT / 'build/generated'
TRANSPORT = '''BlockingDuplexByteStream I2cTransport Iap2ControlDeadline Iap2CsmChannel
Iap2IdentificationClient Iap2LinkChannel Iap2LinkEngine Iap2LocationClient
Iap2VehicleStatus Iap2WiredControlClient Iap2WirelessControlClient'''.split()
MEDIA = ['TouchLatencyProbe', 'MediaCodecSupport']


def prepare():
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir(parents=True)
    selected = []
    for folder in ('airplay', 'iap2', 'mfi'):
        selected += list((UPSTREAM / folder).rglob('*.kt'))
    selected = [p for p in selected if p.name != 'CarPlayMediaButton.kt']
    selected += [UPSTREAM / 'transport' / f'{name}.kt' for name in TRANSPORT]
    selected += [UPSTREAM / 'media' / f'{name}.kt' for name in MEDIA]
    for src in sorted(selected):
        text = src.read_text().replace('android.util.Log', 'dev.diplay.desktop.DesktopLog')
        # Semantics-preserving compatibility for the optional Kotlin 1.9 offline builder.
        text = text.replace('private val n = BigInteger(N_HEX, 16)',
                            'private val n by lazy { BigInteger(N_HEX, 16) }')
        text = text.replace('if (failure != null) throw failure', 'failure?.let { throw it }')
        text = text.replace('import dev.diplay.desktop.DesktopLog\n',
                            'import dev.diplay.desktop.DesktopLog as Log\n')
        if re.search(r'\b(?:android|androidx)\.', text):
            raise SystemExit(f'Unexpected Android dependency: {src.relative_to(UPSTREAM)}')
        dest = GENERATED / src.relative_to(UPSTREAM)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    text = (UPSTREAM / 'transport/IphoneUsbHost.kt').read_text()
    tail = text[text.index('sealed class IphoneUsbException'):].strip()
    if 'android.' in tail or not tail.endswith('}'):
        raise SystemExit('Upstream exception layout changed; review the source adapter')
    (GENERATED / 'transport/IphoneUsbException.kt').write_text(
        'package com.shilapi.xcertplay.transport\nimport java.io.IOException\n' + tail + '\n')
    print(f'Prepared {len(selected)} upstream Kotlin files (no Android runtime)', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'build', 'test'], nargs='?', default='build')
    parser.add_argument('--bcprov', default=os.environ.get('BCPROV_JAR', '/usr/share/java/bcprov.jar'))
    parser.add_argument('--compiler-heap-mb', type=int, default=1024,
                        help='Build-time JVM heap, not receiver runtime memory (default: 1024)')
    args = parser.parse_args()
    if not 256 <= args.compiler_heap_mb <= 8192:
        parser.error('compiler-heap-mb must be in 256..8192')
    prepare()
    if args.action == 'prepare':
        return
    compiler = shutil.which('kotlinc')
    if not compiler or not Path(args.bcprov).is_file():
        parser.error('Install Kotlin >= 1.9 and libbcprov-java, or use the standalone Gradle build')
    sources = sorted(str(p) for p in GENERATED.rglob('*.kt'))
    sources += sorted(str(p) for p in (ROOT / 'src/main/kotlin').rglob('*.kt'))
    jar = ROOT / 'build/diplay-core.jar'
    subprocess.run([compiler, f'-J-Xmx{args.compiler_heap_mb}m', *sources,
                    '-jvm-target', '17', '-cp', args.bcprov,
                    '-include-runtime', '-d', str(jar)], check=True)
    shutil.copyfile(args.bcprov, ROOT / 'build/bcprov.jar')
    if args.action == 'test':
        subprocess.run(['java', '-cp', f'{jar}:{ROOT / "build/bcprov.jar"}',
                        'dev.diplay.desktop.MainKt', '--self-test'], check=True)


if __name__ == '__main__':
    main()
