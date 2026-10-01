import hashlib
from datetime import datetime, timedelta, timezone
import stat
import zipfile
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from diplay_linux.identity import validate_identity, from_apk, install_identity, inspect_directory, PREFIX, FILES, MAX_IDENTITY


def synthetic_identity():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SYNTHETIC TEST ONLY - NOT APPLE TRUSTED')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=1)).sign(key, hashes.SHA256()))
    encoded = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return encoded, pkcs7.serialize_certificates([cert], serialization.Encoding.DER)


def synthetic_apk(path, material=None):
    material = material or synthetic_identity()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in zip(FILES, material): archive.writestr(PREFIX+name, data)
        archive.writestr('../../escape', 'must not be extracted')
    return path


def test_import_and_verify_owner_only_idempotent(tmp_path):
    apk = synthetic_apk(tmp_path/'test.apk'); target = tmp_path/'private-identity'
    expected = hashlib.sha256(apk.read_bytes()).hexdigest()
    result = install_identity(apk, target, expected)
    assert result['key_matches_certificate'] and result['within_validity_window']
    assert result['apple_trust'] == 'NOT_VERIFIED' and not result['already_installed']
    assert target.stat().st_mode & 0o777 == 0o700
    assert all((target/n).stat().st_mode & 0o777 == 0o600 for n in FILES)
    assert not (tmp_path/'escape').exists()
    assert install_identity(apk, target, expected)['already_installed']
    assert inspect_directory(target)['certificate_sha256'] == result['certificate_sha256']


def test_mismatched_key_rejected():
    one, two = synthetic_identity(), synthetic_identity()
    with pytest.raises(ValueError, match='does not match'): validate_identity(one[0], two[1])


def test_refuse_overwrite(tmp_path):
    apk = synthetic_apk(tmp_path/'one.apk'); target = tmp_path/'identity'
    install_identity(apk, target)
    apk2 = synthetic_apk(tmp_path/'two.apk')
    with pytest.raises(ValueError, match='refusing to overwrite'): install_identity(apk2, target)


def test_hash_rejected_before_install(tmp_path):
    apk = synthetic_apk(tmp_path/'test.apk')
    with pytest.raises(ValueError, match='SHA-256'): install_identity(apk, tmp_path/'identity', '0'*64)
    assert not (tmp_path/'identity').exists()


def test_source_only_apk_rejected(tmp_path):
    apk = tmp_path/'source.apk'
    with zipfile.ZipFile(apk, 'w') as archive: archive.writestr('AndroidManifest.xml', 'not relevant')
    with pytest.raises(ValueError, match='exactly one'): from_apk(apk)


def test_duplicate_key_rejected(tmp_path):
    apk = synthetic_apk(tmp_path/'dup.apk')
    with zipfile.ZipFile(apk, 'a') as archive:
        with pytest.warns(UserWarning): archive.writestr(PREFIX+FILES[0], b'bad')
    with pytest.raises(ValueError, match='exactly one'): from_apk(apk)


def test_oversized_and_symlink_entries_rejected(tmp_path):
    for symlink in (False, True):
        apk = tmp_path/('symlink.apk' if symlink else 'huge.apk')
        with zipfile.ZipFile(apk, 'w') as archive:
            item = zipfile.ZipInfo(PREFIX+FILES[0])
            if symlink: item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, b'x' if symlink else b'x'*(MAX_IDENTITY+1))
        with pytest.raises(ValueError, match='Invalid credential ZIP entry'): from_apk(apk)


def test_private_paths_reject_symlink_and_public_modes(tmp_path):
    apk = synthetic_apk(tmp_path/'test.apk'); target = tmp_path/'private'
    install_identity(apk, target)
    link = tmp_path/'link'; link.symlink_to(target)
    with pytest.raises(ValueError): inspect_directory(link)
    (target/FILES[0]).chmod(0o644)
    with pytest.raises(ValueError): inspect_directory(target)
