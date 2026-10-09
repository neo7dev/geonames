import hashlib

from geonames_server.api.auth import API_KEY_PREFIX, generate_api_key, hash_api_key


def test_generated_key_has_prefix_and_256_random_bits():
    key, _, _ = generate_api_key()

    assert key.startswith(API_KEY_PREFIX)
    # token_urlsafe(32): 32 random bytes as 43 base64url characters
    assert len(key) == len(API_KEY_PREFIX) + 43


def test_display_prefix_is_the_start_of_the_key():
    key, prefix, _ = generate_api_key()

    assert prefix == key[: len(API_KEY_PREFIX) + 8]


def test_hash_is_sha256_of_the_key():
    key, _, key_hash = generate_api_key()

    assert key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert hash_api_key(key) == key_hash


def test_keys_are_unique():
    assert len({generate_api_key()[0] for _ in range(100)}) == 100
