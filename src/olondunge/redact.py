"""Secret redaction for everything Olondunge writes to disk or returns to a host.

Two failure directions matter and they pull against each other. Missing a real key leaks
it into a transcript, a ledger, or a host's context. Redacting ordinary prose corrupts
the answer a worker produced: the defect this module was rewritten for turned a Python
signature `persist(token: AdmittedVerdict)` into `persist(token: [REDACTED]`, which
changed the meaning of a research reply while no secret was present at all.

So the rules key on the SHAPE of a credential rather than on a keyword. A vendor prefix
must be followed by a key length body. A `token: value` pair is redacted only when the
value looks machine generated: it carries a digit, or it is long, or it was quoted. A
capitalized identifier such as a type name has none of those properties.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"
    r"|xai-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[A-Za-z0-9_\-]{30,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|whsec_[A-Za-z0-9]{24,}"
    r")"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=\-]{16,}")
# Basic credentials are base64; requiring a digit, "+", "/" or "=" keeps prose such as
# "Basic authentication" intact.
_BASIC_RE = re.compile(r"(?i)\b(Basic)\s+(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{12,}={0,2}")
# After an Authorization header the scheme word is unambiguous, so any base64 value goes.
_BASIC_HEADER_RE = re.compile(r"(?i)\b(Authorization\s*:\s*Basic)\s+[A-Za-z0-9+/]{4,}={0,2}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
# Scheme and userinfo lengths are bounded so a long `a-a-a-...` run, or many `://` on one
# line, cannot make the scan quadratic (OBSERVED: 2.5 s for 40 KB with unbounded `*`).
_CRED_URL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{0,31}://)[^\s/:@]{1,256}:[^\s/@]{1,256}@")
# A key may carry a namespace in front (`DB_PASSWORD`, `STRIPE_SECRET_KEY`,
# `aws_secret_access_key`); the name must still END in a credential word. The namespace is
# bounded (six segments of up to 32 characters) so the scan stays linear: an unbounded
# `(?:[A-Za-z0-9]+[_-])*` retries from every hyphen of a long `a-a-a-...` run.
_KV_RE = re.compile(
    r"""(?ix)
    (?P<key>(?<![\w])["']?
        (?:
          [A-Za-z0-9]{0,16}(?P<pwname>password|passwd|passphrase)
        | (?:[A-Za-z0-9]{1,32}[_-]){0,6}
          (?P<name>password|passwd|pwd|passphrase|api[_-]?key|access[_-]?key
            |access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret
            |secret[_-]?key|private[_-]?key|token|secret)
        )
    ["']?(?P<sep>\s*[:=]\s*))
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;)}\]]+)
    """
)
_PASSWORD_NAMES = frozenset({"password", "passwd", "pwd", "passphrase"})


def _looks_secret(value: str) -> bool:
    if value[:1] in {"'", '"'}:
        inner = value[1:-1]
        return len(inner) >= 6 and not inner.startswith("$")
    if value.startswith("$") or value.startswith("<") or value.lower() in {"none", "null"}:
        return False
    has_digit = any(ch.isdigit() for ch in value)
    has_alpha = any(ch.isalpha() for ch in value)
    if len(value) >= 8 and has_digit and has_alpha:
        return True
    # A long value with no digit is a secret only when it is not a plain identifier or a
    # dotted name: `token: AdmittedVerdictFactoryForLongNames` is code, not a key.
    return len(value) >= 32 and _IDENTIFIER_RE.fullmatch(value) is None


def _looks_like_password(value: str, env_style: bool) -> bool:
    """A password carries no machine shape. Any plain value counts from eight characters
    after `:` (so `password: str` in code survives), or from four in a `NAME=value`
    assignment, unless it is a reference: `$VAR`, `<placeholder>`, a dotted, bracketed or
    called name, or a snake_case variable passed through (`password=db_password`)."""

    if value[:1] in {"'", '"', "$", "<"} or any(ch in value for ch in ".[("):
        return False
    if env_style:
        return len(value) >= 4 and "_" not in value
    return len(value) >= 8


def _looks_like_env_secret(value: str) -> bool:
    """`GITLAB_TOKEN=abcdefghijklmnopqrstuvwxyz`: a long unbroken value assigned with no
    spaces, the shape of an environment or config line, even without a digit."""

    return (
        len(value) >= 16
        and value[:1] not in {"'", '"', "$", "<"}
        and re.fullmatch(r"[A-Za-z0-9+/=-]+", value) is not None
    )


def _kv_sub(match: re.Match[str]) -> str:
    value = match.group("value")
    name = (match.group("pwname") or match.group("name") or "").lower()
    sep = match.group("sep")
    env_style = sep == "="
    # `self.password = password` is code: a spaced `=` gets only the machine shape test.
    code_assignment = "=" in sep and not env_style
    password_rule = (
        name in _PASSWORD_NAMES
        and not code_assignment
        and value.lower() != name
        and _looks_like_password(value, env_style)
    )
    if not (_looks_secret(value) or password_rule or (env_style and _looks_like_env_secret(value))):
        return match.group(0)
    quote = value[0] if value[:1] in {"'", '"'} else ""
    return f"{match.group('key')}{quote}{REDACTED}{quote}"


def redact(text: str) -> str:
    """Replace credential shaped substrings with [REDACTED]. Never raises on str input."""

    if not text:
        return text
    out = _PEM_RE.sub(REDACTED, text)
    out = _CRED_URL_RE.sub(lambda m: f"{m.group(1)}{REDACTED}@", out)
    out = _KV_RE.sub(_kv_sub, out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", out)
    out = _BASIC_HEADER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", out)
    out = _BASIC_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", out)
    out = _PREFIX_RE.sub(REDACTED, out)
    out = _JWT_RE.sub(REDACTED, out)
    return out
