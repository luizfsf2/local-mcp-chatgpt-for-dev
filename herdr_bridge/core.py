"""Small, deliberately restricted adapter for the Herdr Core CLI.

Only CLI operations, no shell interpolation or generic terminal typing. This
implementation targets Herdr Core (`hc`), not the separate Herdr TUI runtime.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any, Callable


class BridgeError(RuntimeError):
    """An operation was denied or Herdr could not complete it."""


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.S)
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1a\x1c-\x1f\x7f]")
MAX_MESSAGE = 8000
MAX_SCREEN = 24000
MAX_LINES = 200


def run_hc(executable: str, args: list[str], timeout: float = 30) -> str:
    """Run a local hc subcommand; *never* invoke a system shell.

    Text is supplied as one argv item, so quotes/newlines in a user message
    cannot turn into PowerShell, bash, or cmd.exe commands at this boundary.
    """
    try:
        result = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            env={k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"},
        )
    except FileNotFoundError as exc:
        raise BridgeError(
            f"Herdr Core nao encontrado ({executable!r}). Instale o hc e confira o PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"hc {args[0]} excedeu {timeout:g}s. Confira o terminal antes de repetir.") from exc
    if result.returncode != 0:
        # Output could contain secrets or terminal text; limit what is surfaced.
        error = (result.stderr or result.stdout or "sem detalhes").strip()[-600:]
        raise BridgeError(f"hc {args[0]} falhou (exit={result.returncode}): {error}")
    return result.stdout


def _parse_json(text: str, operation: str) -> Any:
    try:
        return json.loads(text.lstrip("\ufeff").strip())
    except (ValueError, TypeError) as exc:
        raise BridgeError(f"hc {operation} nao retornou JSON valido. Confira hc --version.") from exc


def _terminal_list(data: Any) -> list[dict]:
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        return data
    if isinstance(data, dict) and isinstance(data.get("terminals"), list):
        if all(isinstance(x, dict) for x in data["terminals"]):
            return data["terminals"]
    raise BridgeError("Formato de 'hc ls --json' inesperado; nenhuma sessao foi liberada.")


def _terminal_record(data: Any) -> dict:
    if isinstance(data, dict):
        if isinstance(data.get("terminal"), dict):
            return data["terminal"]
        if isinstance(data.get("address"), str):
            return data
    raise BridgeError("Formato de 'hc get --json' inesperado; sessao nao verificada.")


def _clean_screen(text: str) -> str:
    """Remove terminal control characters but not ordinary text."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    return _CONTROLS.sub("", text)


def _status(rec: dict) -> str:
    val = rec.get("status")
    if isinstance(val, str) and val:
        return val
    if isinstance(val, dict):
        for field in ("state", "status"):
            if isinstance(val.get(field), str) and val[field]:
                return val[field]
    return "unknown"


def _state(rec: dict) -> str:
    val = rec.get("state")
    return val.lower() if isinstance(val, str) else "unknown"


def _agent(rec: dict) -> str | None:
    """Use a Herdr-reported identity; do not infer from terminal text or cwd."""
    val = rec.get("agent")
    if isinstance(val, str) and val.lower() in ("claude", "codex"):
        return val.lower()
    return None


def _foreground_is_shell(rec: dict) -> bool:
    """Fail closed when the observable foreground is a known interactive shell.

    This guards against stale OSC status after a CLI exits back to PowerShell;
    Herdr's status-clearing behavior on Windows is not equivalent to Unix.
    """
    fg = rec.get("foreground")
    if not isinstance(fg, dict):
        return False  # May be unavailable; the Herdr agent identity is still required.
    name = fg.get("name")
    if not isinstance(name, str):
        return False
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.removesuffix(".exe") in {
        "powershell", "pwsh", "cmd", "bash", "sh", "zsh", "fish", "nu", "nushell",
    }


def _path_is_allowed(cwd: Any, root: Path) -> bool:
    if not isinstance(cwd, str) or not cwd:
        return False
    path = Path(cwd)
    if not path.is_absolute():
        return False
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def _validate_name(name: str, purpose: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise BridgeError(f"{purpose} deve conter apenas letras minusculas, numeros, _ ou -, ate 32 caracteres.")
    return name


class Bridge:
    def __init__(
        self,
        config_path: Path,
        runner: Callable[[str, list[str], float], str] = run_hc,
    ) -> None:
        self.path = Path(config_path)
        if not self.path.is_file():
            raise BridgeError(f"Configure projetos primeiro: arquivo ausente {self.path}")
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise BridgeError(f"Nao foi possivel ler a configuracao: {exc}") from exc
        if not isinstance(config, dict):
            raise BridgeError("Configuracao deve ser um objeto JSON.")
        self.config = config
        self.hc = config.get("hc_executable", "hc")
        if not isinstance(self.hc, str) or not self.hc.strip():
            raise BridgeError("hc_executable invalido.")
        projects = config.get("projects", {})
        if not isinstance(projects, dict) or not projects:
            raise BridgeError("Cadastre ao menos um projeto em projects.")
        self.projects: dict[str, dict] = {}
        for name, spec in projects.items():
            _validate_name(name, "Nome de projeto")
            if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
                raise BridgeError(f"Projeto {name} precisa de path.")
            root = Path(os.path.expandvars(os.path.expanduser(spec["path"]))).resolve(strict=False)
            agents = spec.get("agents", ["claude", "codex"])
            if not isinstance(agents, list) or not agents or any(a not in ("claude", "codex") for a in agents):
                raise BridgeError(f"Agentes invalidos no projeto {name}.")
            self.projects[name] = {"path": root, "agents": agents}
        self.attached = config.get("attached_sessions", {})
        if not isinstance(self.attached, dict):
            raise BridgeError("attached_sessions deve ser objeto.")
        for alias, spec in self.attached.items():
            _validate_name(alias, "Alias de sessao")
            if not isinstance(spec, dict) or spec.get("project") not in self.projects:
                raise BridgeError(f"Sessao {alias}: projeto invalido.")
            if not isinstance(spec.get("terminal"), str) or not spec["terminal"]:
                raise BridgeError(f"Sessao {alias}: terminal invalido.")
            if spec.get("agent") not in self.projects[spec["project"]]["agents"]:
                raise BridgeError(f"Sessao {alias}: agente invalido.")
        cmd = config.get("agent_commands", {"claude": ["claude"], "codex": ["codex"]})
        if not isinstance(cmd, dict):
            raise BridgeError("agent_commands deve ser objeto.")
        self.commands: dict[str, list[str]] = {}
        for name in ("claude", "codex"):
            arr = cmd.get(name, [name])
            if not isinstance(arr, list) or not arr or any(not isinstance(x, str) or not x for x in arr):
                raise BridgeError(f"agent_commands[{name}] deve ser uma lista de argumentos.")
            self.commands[name] = arr
        self.runner = runner
        self.allow_launch = config.get("allow_launch", False) is True
        self.allow_send = config.get("allow_send", False) is True
        self.allow_interrupt = config.get("allow_interrupt", False) is True

    def _call(self, *args: str, timeout: float = 30) -> str:
        return self.runner(self.hc, list(args), timeout)

    def _ls(self) -> list[dict]:
        return _terminal_list(_parse_json(self._call("ls", "--all", "--json"), "ls"))

    def _get(self, address: str) -> dict:
        rec = _terminal_record(_parse_json(self._call("get", "--json", address), "get"))
        if rec.get("address") != address:
            raise BridgeError("hc get retornou outra sessao; operacao cancelada.")
        return rec

    def list_projects(self) -> list[dict]:
        return [
            {"project": name, "path": str(info["path"]), "available": info["path"].is_dir(), "agents": info["agents"]}
            for name, info in self.projects.items()
        ]

    def _admission(self, rec: dict) -> dict | None:
        """Both explicit local authorization and filesystem scope are required."""
        address, name = rec.get("address"), rec.get("name")
        if not isinstance(address, str) or not address:
            return None
        for alias, spec in self.attached.items():
            if spec["terminal"] in (address, name):
                project = spec["project"]
                if _path_is_allowed(rec.get("cwd"), self.projects[project]["path"]):
                    return {"alias": alias, "project": project, "agent": spec["agent"], "source": "configured"}
                return None
        labels = rec.get("labels")
        if not isinstance(labels, dict) or labels.get("agent_bridge") != "yes":
            return None
        project, agent = labels.get("project"), labels.get("agent")
        if project not in self.projects or agent not in self.projects[project]["agents"]:
            return None
        if not _path_is_allowed(rec.get("cwd"), self.projects[project]["path"]):
            return None
        return {"alias": name if isinstance(name, str) and name else address,
                "project": project, "agent": agent, "source": "labeled"}

    def _sessions(self) -> list[dict]:
        allowed: list[dict] = []
        for rec in self._ls():
            permit = self._admission(rec)
            if permit:
                allowed.append({
                    **permit,
                    "address": rec["address"],
                    "name": rec.get("name"),
                    "state": _state(rec),
                    "status": _status(rec),
                    "reported_agent": _agent(rec),
                })
        return allowed

    def list_sessions(self) -> list[dict]:
        """Returns only locally authorized Herdr Core sessions."""
        return self._sessions()

    def _authorize(self, session: str, for_write: bool = False) -> tuple[dict, dict]:
        if not isinstance(session, str) or not session:
            raise BridgeError("Informe uma sessao listada por list_sessions.")
        candidates = [s for s in self._sessions() if session in (s["address"], s["alias"]) ]
        if len(candidates) != 1:
            raise BridgeError("Sessao nao autorizada, inexistente ou ambigua. Consulte list_sessions.")
        s = candidates[0]
        latest = self._get(s["address"])
        renewed = self._admission(latest)
        if not renewed or renewed["project"] != s["project"] or renewed["agent"] != s["agent"]:
            raise BridgeError("A sessao mudou de projeto/autorizacao. Operacao cancelada.")
        if for_write:
            if _state(latest) != "live":
                raise BridgeError("Sessao nao esta ativa; nenhuma tecla enviada.")
            if _status(latest) in ("blocked", "error"):
                raise BridgeError(f"Sessao {_status(latest)}: consulte a tela e decida localmente; sem aprovacoes automaticas.")
            if _agent(latest) != s["agent"] or _foreground_is_shell(latest):
                raise BridgeError(
                    f"Agente em primeiro plano nao confirmado como {s['agent']}. "
                    "Nao enviarei texto que possa cair num shell comum."
                )
        return s, latest

    def get_session(self, session: str) -> dict:
        permit, rec = self._authorize(session)
        return {
            **permit,
            "cwd": rec.get("cwd"),
            "state": _state(rec),
            "status": _status(rec),
            "reported_agent": _agent(rec),
            "can_send": (_state(rec) == "live" and _agent(rec) == permit["agent"]
                         and not _foreground_is_shell(rec)
                         and _status(rec) not in ("blocked", "error") and self.allow_send),
            "status_source": "herdr_osc_or_unknown",
        }

    def read_session(self, session: str, lines: int = 60) -> dict:
        if not isinstance(lines, int) or not 0 <= lines <= MAX_LINES:
            raise BridgeError(f"lines deve estar entre 0 e {MAX_LINES}.")
        permit, rec = self._authorize(session)
        output = _clean_screen(self._call("read", "--lines", str(lines), permit["address"], timeout=40))
        truncated = len(output) > MAX_SCREEN
        if truncated:
            output = output[-MAX_SCREEN:]
        return {
            "session": permit["address"],
            "status": _status(rec),
            "state": _state(rec),
            "screen": output,
            "truncated": truncated,
            "note": "Saida de terminal, nao eventos de ferramentas. Pode estar incompleta quando ocupado.",
        }

    def send_message(self, session: str, message: str) -> dict:
        if not self.allow_send:
            raise BridgeError("Envio desabilitado em config.json (allow_send).")
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
            raise BridgeError(f"Mensagem deve ter de 1 a {MAX_MESSAGE} caracteres.")
        permit, rec = self._authorize(session, for_write=True)
        # Herdr itself also denies blocked sessions; --force is never used.
        result = _parse_json(self._call("send", "--json", permit["address"], message, timeout=100), "send")
        return {
            "accepted_by_hc": True,
            "session": permit["address"],
            "previous_status": _status(rec),
            "response": result,
            "note": "Mensagem entregue ao Herdr, nao comprova conclusao da tarefa. Consulte get_session/read_session.",
        }

    def start_agent(self, project: str, agent: str) -> dict:
        if not self.allow_launch:
            raise BridgeError("Criacao desabilitada em config.json (allow_launch).")
        if project not in self.projects:
            raise BridgeError("Projeto nao autorizado.")
        if agent not in self.projects[project]["agents"]:
            raise BridgeError("Agente nao permitido neste projeto.")
        root = self.projects[project]["path"]
        if not root.is_dir():
            raise BridgeError(f"Pasta do projeto nao existe: {root}")
        suffix = secrets.token_hex(3)
        name = f"ab-{project[:12]}-{agent}-{suffix}"[:32]
        params = (
            "run", "--json", "--bg", "--no-parent", "--name", name,
            "--cwd", str(root),
            "--label", "agent_bridge=yes", "--label", f"project={project}",
            "--label", f"agent={agent}",
            *self.commands[agent],
        )
        result = _parse_json(self._call(*params, timeout=40), "run")
        # Do not claim it is ready: reported identity may take time to appear.
        return {"started_by_hc": True, "name": name, "result": result,
                "note": "Use list_sessions/get_session para descobrir o id e confirmar o agente antes de enviar."}

    def wait_session(self, session: str, seconds: int = 8) -> dict:
        if not isinstance(seconds, int) or seconds < 1 or seconds > 15:
            raise BridgeError("seconds deve estar entre 1 e 15.")
        permit, _ = self._authorize(session)
        outcome = _parse_json(
            self._call("wait", "--json", "--timeout", str(seconds), permit["address"], timeout=seconds + 8),
            "wait",
        )
        # Timeout is a normal check-in, not proof of task completion.
        return {"session": permit["address"], "wait_result": outcome,
                "note": "Um timeout nao significa falha; done/blocked sao sinais reportados pelo agente, nao validacao da tarefa."}

    def interrupt_session(self, session: str) -> dict:
        if not self.allow_interrupt:
            raise BridgeError("Interrupcao desabilitada em config.json (allow_interrupt).")
        permit, _ = self._authorize(session, for_write=True)
        result = _parse_json(
            self._call("interrupt", "--json", "--timeout", "5", permit["address"], timeout=14),
            "interrupt",
        )
        return {"session": permit["address"], "result": result,
                "note": "Interrupcao solicitada; confira o estado antes de assumir que parou."}
