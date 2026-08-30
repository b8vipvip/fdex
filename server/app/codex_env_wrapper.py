from __future__ import annotations

import os
import sys
from pathlib import Path

# This wrapper is intentionally small. FDEX launches it with an already reduced environment,
# then it strips the process environment again immediately before exec'ing the official Codex
# runtime. The provider key remains available to Codex itself, while Codex's
# shell_environment_policy prevents tool commands from inheriting it.
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


def _inject_governance_args(args: list[str]) -> list[str]:
    """Inject operator-owned Codex config above tenant CODEX_HOME state.

    The wrapper may be launched while cwd points at an arbitrary tenant worktree. Python places
    this file's ``.../server/app`` directory on sys.path, not ``.../server``; add the trusted
    server package root explicitly before importing the policy module. This keeps the import
    independent of cwd/PYTHONPATH without passing PYTHONPATH into Codex.

    CLI --config overrides are inserted before ``app-server``. They have higher precedence than
    persisted user config and are parsed by the official Codex runtime, so FDEX does not create a
    second sub-agent scheduler.
    """
    if "app-server" not in args:
        return list(args)

    server_root = str(Path(__file__).resolve().parents[1])
    if server_root not in sys.path:
        sys.path.insert(0, server_root)

    from app.codex_subagent_governance import codex_subagent_cli_overrides

    insert_at = args.index("app-server")
    injected: list[str] = []
    for override in codex_subagent_cli_overrides():
        injected.extend(("--config", override))
    return [*args[:insert_at], *injected, *args[insert_at:]]


def main() -> int:
    if len(sys.argv) < 2:
        print("missing Codex runtime path", file=sys.stderr)
        return 2
    real_codex = os.path.realpath(sys.argv[1])
    if not os.path.isfile(real_codex) or not os.access(real_codex, os.X_OK):
        print("Codex runtime is not executable", file=sys.stderr)
        return 2

    try:
        codex_args = _inject_governance_args(list(sys.argv[2:]))
    except Exception as exc:
        # Never fall back to an ungoverned official runtime when Center policy cannot be loaded.
        print(f"FDEX Codex governance configuration is invalid: {exc}", file=sys.stderr)
        return 2

    clean_env = {name: os.environ[name] for name in _SAFE_ENV_NAMES if os.environ.get(name)}
    clean_env.setdefault("LANG", "C.UTF-8")
    clean_env.setdefault("LC_ALL", "C.UTF-8")
    clean_env["CI"] = "true"
    os.environ.clear()
    os.environ.update(clean_env)
    os.execve(real_codex, [real_codex, *codex_args], clean_env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
