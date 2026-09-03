"""Strip credentials from captured text before it is stored or re-injected.

The hooks ship the user's prompt and the agent's answer verbatim. Either can
carry an API key pasted for debugging or an ``.env`` value quoted in an
explanation — and once stored, that value rides into every later session and
to anyone holding a key scoped to the project. There is no way to un-inject
it later, so it is masked at capture, and again on the server for clients
that do not run the hooks.

Deliberately pattern-based and conservative: it targets shapes that are
almost never anything but a secret (vendor key prefixes, JWTs, private-key
blocks, ``password=`` assignments, credentials inside URLs). Ordinary code
and prose pass through untouched.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Private key blocks, whole.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # Well-known vendor key shapes.
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),  # Google API key
    re.compile(r"\bjv_[A-Za-z0-9_\-]{16,}\b"),  # MyViking keys themselves
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),  # GitLab
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{30,}\b"),
    # JSON web tokens.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # Bearer / basic auth headers.
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=+/]{16,}"),
    re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{16,}"),
    # Credentials embedded in URLs: scheme://user:secret@host
    re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^@\s/]{3,}(@)"),
)

# key = value style assignments. The name must look like a credential and the
# value must be a single token of some length — this skips ``token = None`` and
# ``password: "" `` and prose like "the password is stored in Vault".
_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z0-9_\-]*(?:api[_\-]?key|access[_\-]?key|secret[_\-]?key|secret|token|"
    r"passwd|password|pwd|client[_\-]?secret|private[_\-]?key|auth[_\-]?token|"
    r"refresh[_\-]?token|session[_\-]?key|signing[_\-]?key)"
    r"[A-Za-z0-9_\-]*)"  # allow suffixes like API_KEY_PROD
    r"(\s*[:=]\s*)([\"']?)"
    r"([^\s\"'`,;]{8,})"
)
_ASSIGN_SKIP = {"none", "null", "nil", "true", "false", "undefined", "redacted"}
_PLACEHOLDER_PREFIXES = ("$", "${", "{{", "<", "process.env", "os.environ", "env.")


def redact(text: str) -> str:
    """Return ``text`` with credential-shaped substrings masked."""
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        if pat.groups:
            # Keep the captured prefix/suffix (e.g. "Bearer ", "://user:", "@").
            def _keep(m: re.Match[str]) -> str:
                groups = m.groups()
                if len(groups) == 2:
                    return f"{groups[0]}{REDACTED}{groups[1]}"
                return f"{groups[0]}{REDACTED}"

            out = pat.sub(_keep, out)
        else:
            out = pat.sub(REDACTED, out)

    def _assign(m: re.Match[str]) -> str:
        name, sep, quote, value = m.group(1), m.group(2), m.group(3), m.group(4)
        low = value.lower()
        if low in _ASSIGN_SKIP or low == REDACTED.lower():
            return m.group(0)
        # A reference to another variable or a placeholder is not a secret.
        if value.startswith(_PLACEHOLDER_PREFIXES):
            return m.group(0)
        if not quote:
            # Unquoted: ``token = tokenizer.encode(text)`` and ``pwd = os.getcwd()``
            # are code, not credentials. Mask only when the value looks like a
            # literal secret — carries a digit and is not a call or a bare
            # identifier path.
            if "(" in value or value.isdigit() or not re.search(r"\d", value):
                return m.group(0)
        return f"{name}{sep}{quote}{REDACTED}"

    return _ASSIGN.sub(_assign, out)


def redacted(text: str) -> bool:
    return REDACTED in (text or "")
