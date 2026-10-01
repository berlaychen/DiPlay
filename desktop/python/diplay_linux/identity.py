"""Import upstream runtime authentication without executing the APK or exposing keys.

Public source/CI artifacts stay credential-free. Importing a user-selected APK or
explicitly fetching the pinned public APK is a LOCAL deployment action.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import pkcs7

VERSION = '0.2.8'
UPSTREAM_URL = f'https://github.com/shihabal3amri/DiPlay/releases/download/v{VERSION}/DiPlay-{VERSION}.apk'
UPSTREAM_SHA256 = '9b36a0866608244e422053d6027706b4672d8f2f4a8be9eb7e612411ffb6bf48'
FILES = ('identity.pk8', 'certificate.p7b')
PREFIX = 'assets/offline-mfi/'
MAX_IDENTITY = 16384
MAX_APK = 128 * 1024 * 1024
DEFAULT_DIR = Path.home() / '.config/diplay/identity'


def validate_identity(key_data, certificate_data):
    """Check the JVM key/curve contract, NOT Apple's trust or provisioning rights."""
    if any(not 0 < len(x) <= MAX_IDENTITY for x in (key_data, certificate_data)):
        raise ValueError('Identity file is empty or too large')
    key = serialization.load_der_private_key(key_data, password=None)
    certs = pkcs7.load_der_pkcs7_certificates(certificate_data)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('Expected a P-256 EC private key')
    if len(certs) != 1:
        raise ValueError('Expected one accessory certificate')
    cert = certs[0]
    public = cert.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, ec.SECP256R1):
        raise ValueError('Expected a P-256 accessory certificate')
    if key.public_key().public_numbers() != public.public_numbers():
        raise ValueError('Private key does not match the accessory certificate')
    digest = os.urandom(32)
    signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    public.verify(signature, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    now = datetime.now(timezone.utc)
    start = getattr(cert, 'not_valid_before_utc', None)
    end = getattr(cert, 'not_valid_after_utc', None)
    if start is None:
        start = cert.not_valid_before.replace(tzinfo=timezone.utc)
        end = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return dict(key_matches_certificate=True, curve='secp256r1',
                certificate_sha256=cert.fingerprint(hashes.SHA256()).hex(),
                not_before=start.isoformat(), not_after=end.isoformat(),
                within_validity_window=start <= now <= end, apple_trust='NOT_VERIFIED',
                note='Signature consistency is not proof that an iPhone accepts this identity.')


def from_apk(apk, expected_sha256=None):
    apk = Path(apk)
    if not apk.is_file() or not 0 < apk.stat().st_size <= MAX_APK:
        raise ValueError('Invalid APK size')
    with apk.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError('APK SHA-256 does not match the selected release')
    data = {}
    with zipfile.ZipFile(apk) as archive:
        for name in FILES:
            matches = [i for i in archive.infolist() if i.filename == PREFIX + name]
            if len(matches) != 1:
                raise ValueError(f'APK must contain exactly one {PREFIX}{name}; source-only builds do not')
            info = matches[0]
            if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1 or not 0 < info.file_size <= MAX_IDENTITY:
                raise ValueError('Invalid credential ZIP entry')
            with archive.open(info) as stream:
                data[name] = stream.read(MAX_IDENTITY + 1)
            if len(data[name]) != info.file_size:
                raise ValueError('Invalid credential ZIP length')
    report = validate_identity(data[FILES[0]], data[FILES[1]])
    report['apk_sha256'] = digest
    return data, report


def inspect_directory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError('Identity directory must be real and owner-only (0700)')
    data = []
    for name in FILES:
        fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_IDENTITY:
                raise ValueError('Identity files must be owner-only regular files (0600)')
            data.append(stream.read(MAX_IDENTITY + 1))
    return validate_identity(*data)


def install_identity(apk, destination, expected_sha256=None):
    data, report = from_apk(apk, expected_sha256)
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        if inspect_directory(destination)['certificate_sha256'] != report['certificate_sha256']:
            raise ValueError('Destination contains a different identity; refusing to overwrite it')
        report['already_installed'] = True
        return report
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix='.identity-', dir=destination.parent))
    staging.chmod(0o700)
    try:
        for name in FILES:
            fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(data[name]); output.flush(); os.fsync(output.fileno())
        provenance = dict(report,
                          upstream_version=VERSION if report['apk_sha256'] == UPSTREAM_SHA256 else 'user-supplied',
                          source='explicit APK import', experimental=True)
        fd = os.open(staging / 'provenance.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(provenance, output, indent=2)
        inspect_directory(staging)
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    report['already_installed'] = False
    return report


def fetch_upstream(destination):
    with tempfile.TemporaryDirectory(prefix='diplay-upstream-') as directory:
        apk = Path(directory) / 'release.apk'
        request = urllib.request.Request(UPSTREAM_URL, headers={'User-Agent': 'DiPlay-Linux-identity-import'})
        with urllib.request.urlopen(request, timeout=60) as response, apk.open('wb') as output:
            if not response.geturl().startswith('https://'):
                raise ValueError('Refusing insecure download redirect')
            count = 0
            while chunk := response.read(64 * 1024):
                count += len(chunk)
                if count > MAX_APK:
                    raise ValueError('Upstream APK exceeds size limit')
                output.write(chunk)
        return install_identity(apk, destination, UPSTREAM_SHA256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    imported = commands.add_parser('import-apk')
    imported.add_argument('apk', type=Path)
    imported.add_argument('--sha256')
    imported.add_argument('--destination', type=Path, default=DEFAULT_DIR)
    imported.add_argument('--accept-experimental-identity', action='store_true', required=True)
    download = commands.add_parser('fetch-upstream')
    download.add_argument('--destination', type=Path, default=DEFAULT_DIR)
    download.add_argument('--accept-experimental-identity', action='store_true', required=True)
    inspected = commands.add_parser('check')
    inspected.add_argument('--directory', type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    try:
        if args.action == 'check':
            report = inspect_directory(args.directory)
        elif args.action == 'import-apk':
            report = install_identity(args.apk, args.destination, args.sha256)
        else:
            report = fetch_upstream(args.destination)
        print(json.dumps(report, indent=2))
    except Exception as error:
        parser.exit(2, f'Identity operation failed ({type(error).__name__}): '
                       'check APK/version/hash, matching P-256 credentials, destination and permissions.\n')

if __name__ == '__main__':
    main()
