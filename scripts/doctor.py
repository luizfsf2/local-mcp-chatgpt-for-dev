"""Read-only local preflight. Run from project root: python scripts/doctor.py"""

from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from herdr_bridge import Bridge, BridgeError, run_hc  # noqa: E402


def main() -> int:
    path = Path(os.environ.get("HERDR_BRIDGE_CONFIG", str(Path(__file__).resolve().parents[1] / "config.json")))
    try:
        bridge = Bridge(path)
        print("Config:", path)
        for project in bridge.list_projects():
            print("Projeto:", project)
        print("HC:", run_hc(bridge.hc, ["--version"], timeout=10).strip())
        sessions = bridge.list_sessions()
        print(f"Sessoes autorizadas ({len(sessions)}):")
        for entry in sessions:
            print(" ", entry)
        print("Doctor OK (apenas CLI/config; nao testa ChatGPT nem inicia agentes).")
        return 0
    except BridgeError as exc:
        print("Doctor falhou:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
