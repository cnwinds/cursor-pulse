import base64
import os

from pulse.ingestion.crypto import decrypt_secret, encrypt_secret, mask_api_key


def test_encrypt_decrypt_round_trip():
    raw_key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    plaintext = "sk-cursor-test-secret-key-12345"
    blob = encrypt_secret(plaintext, raw_key)
    assert decrypt_secret(blob, raw_key) == plaintext


def test_mask_api_key():
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-ab...mnop"
    assert mask_api_key("short") == "***"
    assert mask_api_key("  sk-abc12345678  ") == "sk-ab...5678"
