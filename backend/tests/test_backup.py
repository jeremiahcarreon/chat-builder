import io

import pytest

from app.backup import CHUNK, BackupError, decrypt_stream, encrypt_stream

PASS = "correct horse battery staple"


@pytest.mark.parametrize("size", [0, 10, CHUNK, CHUNK * 2 + 123])
def test_round_trip(size):
    data = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    sealed = io.BytesIO()
    encrypt_stream(io.BytesIO(data), sealed, PASS)
    out = io.BytesIO()
    decrypt_stream(io.BytesIO(sealed.getvalue()), out, PASS)
    assert out.getvalue() == data


def _sealed(data=b"secret data " * 200_000) -> bytes:
    sealed = io.BytesIO()
    encrypt_stream(io.BytesIO(data), sealed, PASS)
    return sealed.getvalue()


def test_wrong_passphrase():
    with pytest.raises(BackupError, match="Wrong passphrase"):
        decrypt_stream(io.BytesIO(_sealed()), io.BytesIO(), "not the right passphrase")


def test_tampering_is_detected():
    sealed = bytearray(_sealed())
    sealed[-50] ^= 1
    with pytest.raises(BackupError):
        decrypt_stream(io.BytesIO(bytes(sealed)), io.BytesIO(), PASS)


def test_truncation_is_detected():
    sealed = _sealed()
    # Cut off after the first complete chunk: the last chunk present isn't marked final.
    first_len = int.from_bytes(sealed[32:36], "big")
    with pytest.raises(BackupError):
        decrypt_stream(io.BytesIO(sealed[: 36 + first_len]), io.BytesIO(), PASS)


def test_short_passphrase_and_wrong_file():
    with pytest.raises(BackupError, match="at least"):
        encrypt_stream(io.BytesIO(b"x"), io.BytesIO(), "short")
    with pytest.raises(BackupError, match="isn't a Chat Builder backup"):
        decrypt_stream(io.BytesIO(b"PK\x03\x04 a zip file"), io.BytesIO(), PASS)
