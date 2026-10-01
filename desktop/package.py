#!/usr/bin/env python3
"""Create a source-inclusive Linux package, optionally with a PRIVATE runtime identity."""
import argparse
import os
from pathlib import Path
import sys
import tarfile
import tempfile
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'python'))
from diplay_linux.identity import FILES, inspect_directory

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--with-identity',type=Path)
    parser.add_argument('--private-deployment-bundle',action='store_true')
    args=parser.parse_args()
    if bool(args.with_identity)!=args.private_deployment_bundle:
        parser.error('--with-identity and --private-deployment-bundle must be used together')
    if not (ROOT/'build/diplay-core.jar').is_file():parser.error('Build the core first')
    if args.with_identity:inspect_directory(args.with_identity)
    destination=args.output.expanduser().absolute()
    if destination.exists():parser.error('Output exists; refusing to overwrite')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.diplay-package-',dir=destination.parent);os.close(fd)
    try:
        with tarfile.open(temporary,'w:gz') as archive:
            roots=[ROOT,ROOT.parent/'shared',ROOT.parent/'LICENSE',ROOT.parent/'docs/licenses',ROOT.parent/'docs/THIRD_PARTY_NOTICES.md']
            for base in roots:
                for path in sorted(base.rglob('*')) if base.is_dir() else [base]:
                    if not path.is_file() or path.is_symlink():continue
                    relative=path.relative_to(ROOT.parent)
                    if any(x in relative.parts for x in ('.git','.gradle','__pycache__','.pytest_cache','generated','smoke','runtime-identity')):continue
                    if path.suffix in ('.pk8','.p7b','.pem','.key','.keystore','.pyc','.apk') or path.name in ('config.local.toml','web-token'):continue
                    if path.resolve() in (destination.resolve(),Path(temporary).resolve()):continue
                    archive.add(path,'DiPlay/'+str(relative),recursive=False)
            if args.with_identity:
                for name in FILES:
                    source=args.with_identity/name
                    info=archive.gettarinfo(str(source),'DiPlay/desktop/runtime-identity/'+name);info.mode=0o600
                    with source.open('rb') as content:archive.addfile(info,content)
        os.chmod(temporary,0o600 if args.with_identity else 0o644)
        os.rename(temporary,destination)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    print('PRIVATE runtime bundle; do not publish.' if args.with_identity else 'Public bundle; no credentials.')
    print(destination)
if __name__=='__main__':main()
