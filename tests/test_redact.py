import pytest

from olondunge.redact import REDACTED, redact


def test_type_annotation_named_token_is_not_redacted() -> None:
    # Defect 06f: the old redactor rewrote this signature and changed a research answer.
    text = "def persist(token: AdmittedVerdict) -> None:"
    assert redact(text) == text


def test_long_identifier_values_are_kept() -> None:
    text = "secret = AdmittedVerdictFactoryForVeryLongNames and token: some.module.path_name_long"
    assert redact(text) == text


def test_vendor_prefixed_keys_are_redacted() -> None:
    synthetic = "sk-ant-" + "abc123XYZ" * 3
    assert REDACTED in redact(f"key is {synthetic} here")
    assert synthetic not in redact(f"key is {synthetic} here")


def test_key_value_with_digits_is_redacted() -> None:
    out = redact('password = "hunter22x"')
    assert "hunter22x" not in out and REDACTED in out


def test_bearer_and_credential_url() -> None:
    out = redact("Authorization: Bearer abcdefghijklmnop1234 https://user:pa55word@example.com/x")
    assert "abcdefghijklmnop1234" not in out
    assert "pa55word" not in out


# Synthetic values only; each is built from repeated fragments, never a real credential.
FAKE = "Zq8vN3pLx7Rt2kWm9Yb4"


@pytest.mark.parametrize(
    "text",
    [
        f"MYSVC_API_KEY={FAKE}",
        f"STRIPE_SECRET_KEY=sk_live_{FAKE}",
        f"the key sk_live_{FAKE} was rotated",
        f"rk_test_{FAKE}",
        f"aws_secret_access_key = {FAKE}Qc6Hd1Fj5Gs0Ae",
        "DB_PASSWORD=Zq8vN3pLx7Rt",
        "password: hunterhunter",
        "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==",
        f'"client_secret": "{FAKE}"',
        "password: Sunshinesky",
        "password: SUNSHINESKY",
        "password: Abcdefgh",
        "Authorization: Basic dXNlcjpwYXNz",
        "PGPASSWORD=Zq8vN3pLx7Rt2kWm",
        "MYSVC_API_KEY=abcdefghijklmnopqrstuvwx",
        "GITLAB_TOKEN=abcdefghijklmnopqrstuvwxyz",
        "DB_PASSWORD=hunter2",
    ],
)
def test_common_secret_shapes_are_redacted(text: str) -> None:
    out = redact(text)
    assert REDACTED in out, text
    for fragment in (
        FAKE,
        "hunterhunter",
        "dXNlcjpwYXNz",
        "Zq8vN3pLx7Rt",
        "unshinesky",
        "UNSHINESKY",
        "bcdefgh",
        "hunter2",
        "klmnopqrstuvwx",
    ):
        assert fragment not in out, text


@pytest.mark.parametrize(
    "text",
    [
        "def login(password: str) -> None:",
        "password: Optional[str] = None",
        "login(password=db_password, user=name)",
        "self.password = password",
        "password = get_password()",
        "Use Basic authentication for the proxy.",
        "interface Login { password: string }",
        "max_tokens: 4096 and num_tokens=12",
        "DB_PASSWORD=$DB_PASSWORD",
        "token: AdmittedVerdict",
    ],
)
def test_prose_and_code_around_secret_words_are_kept(text: str) -> None:
    assert redact(text) == text


def test_long_adversarial_runs_redact_in_linear_time() -> None:
    import time

    for text in ("a-" * 100_000, "ab_" * 70_000, ("Basic " + "A" * 5000 + " ") * 40):
        started = time.perf_counter()
        redact(text)
        assert time.perf_counter() - started < 2.0, text[:12]


def test_a_type_name_after_password_colon_is_redacted_by_design() -> None:
    # A letters only value after `password:` may be a real password, so it is hidden even
    # when it is a type name. Documented in SECURITY.md.
    assert redact("def login(password: SecretStr) -> None:") == (
        "def login(password: [REDACTED]) -> None:"
    )
