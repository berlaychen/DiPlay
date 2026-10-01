from pathlib import Path
import subprocess
import sys
import tarfile
import pytest
from diplay_linux.identity import install_identity
from test_identity import synthetic_apk

ROOT = Path(__file__).resolve().parents[1]

def test_private_bundle_is_explicit_and_private(tmp_path):
    if not (ROOT/'build/diplay-core.jar').exists():
        pytest.skip('Core not built')
    directory = tmp_path/'identity'
    install_identity(synthetic_apk(tmp_path/'test.apk'), directory)
    output = tmp_path/'private.tar.gz'
    refused = subprocess.run([sys.executable, str(ROOT/'package.py'), str(output),
                              '--with-identity', str(directory)], capture_output=True)
    assert refused.returncode != 0 and not output.exists()
    subprocess.run([sys.executable, str(ROOT/'package.py'), str(output), '--with-identity',
                    str(directory), '--private-deployment-bundle'], check=True, capture_output=True)
    assert output.stat().st_mode & 0o777 == 0o600
    with tarfile.open(output) as archive:
        assert archive.getmember('DiPlay/desktop/runtime-identity').mode == 0o700
        for name in ('identity.pk8', 'certificate.p7b'):
            assert archive.getmember('DiPlay/desktop/runtime-identity/'+name).mode == 0o600
        assert not any('web-token' in m.name or '..' in Path(m.name).parts for m in archive.getmembers())
