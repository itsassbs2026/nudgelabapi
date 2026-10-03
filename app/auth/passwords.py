"""Argon2id hashing and the password policy (SPEC §9): at least 12 characters, not a common password, no
composition rules."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12

# Deliberately small: the obviously weak picks (as pingit).
COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "password1234",
    "123456",
    "12345678",
    "123456789",
    "1234567890",
    "123456789012",
    "qwerty",
    "qwerty123",
    "qwertyuiop12",
    "letmein",
    "letmein12345",
    "welcome",
    "welcome123",
    "welcome12345",
    "admin1234",
    "administrator",
    "changeme",
    "changeme123",
    "changeme1234",
    "iloveyou",
    "monkey123",
    "dragon123",
    "football",
    "baseball",
    "trustno1",
    "abc123456",
    "111111111111",
    "123123123123",
    "primecomms123",
    "nudgelab1234",
}


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_policy_violation(plain_password: str) -> str | None:
    """A human-readable reason the password fails the policy, or None if it's fine."""
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if plain_password.lower() in COMMON_PASSWORDS:
        return "Password is too common."
    return None
