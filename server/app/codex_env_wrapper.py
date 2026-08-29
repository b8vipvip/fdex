from __future__ import annotations

import os
import sys

# This wrapper is intentionally tiny. The OpenAI Codex Python SDK launches a child
# process with a copy of the parent environment. FDEX itself contains unrelated
# SMTP/GitHub/admin secrets, so the trusted wrapper strips that environment before
# exec'ing the official Codex runtime. The provider key remains available to Codex
# itself, while the Codex shell_environment_policy configured by codex_engine.py
# prevents tool commands from inheriting it.
_SAFE_ENV_NAMES = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "JAVA_HOME",
    "ANDROID_HOME",
    "ANDROID_SDK_ROOT",
    "CODEX_HOME",
    "FDEX_CODEX_PROVIDER_KEY",
)


def main() -> int:
    if len(sys.argv) < 2:
        print("missing Codex runtime path", file=sys.stderr)
        return 2
    real_codex = os.path.realpath(sys.argv[1])
    if not os.path.isfile(real_codex) or not os.access(real_codex, os.X_OK):
        print("Codex runtime is not executable", file=sys.stderr)
        return 2

    clean_env = {name: os.environ[name] for name in _SAFE_ENV_NAMES if os.environ.get(name)}
    clean_env.setdefault("LANG", "C.UTF-8")
    clean_env.setdefault("LC_ALL", "C.UTF-8")
    clean_env["CI"] = "true"
    os.environ.clear()
    os.environ.update(clean_env)
    os.execve(real_codex, [real_codex, *sys.argv[2:]], clean_env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
