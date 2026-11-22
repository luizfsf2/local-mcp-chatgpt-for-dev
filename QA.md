# QA — Herdr Agent Bridge MCP (protótipo)

- Data: 2026-11-22
- **20 testes unitários aprovados** (`python -m unittest discover -s tests -v`).
- Compilação estática Python: aprovada (`python -m compileall`).
- Testes cobrem: lista de projetos, autorização por labels e configuração explícita, verificação de raiz, leitura, limites de saída, bloqueio de shell/PowerShell em primeiro plano, status bloqueado, agente desconhecido, início em projeto permitido, interrupção desabilitada, `wait` curto, subprocess sem shell e mensagens como argumento único.
- Não instalado no ambiente de QA: `hc`, Claude Code, Codex CLI e o pacote MCP Python SDK. A instalação do SDK não foi possível porque este ambiente não tinha acesso à rede.
- Não executados: testes reais no Windows, integração com o Herdr Core, MCP Inspector ou Secure MCP Tunnel / ChatGPT.

**Gate para marcar como validado em sua máquina:**

1. `hc --version` e `hc ls --json` respondem no Windows.
2. Configure um projeto real e execute `python scripts/doctor.py`.
3. Confirme `hc get <terminal> --json` com `agent: claude`/`codex` antes de testar envio.
4. Execute `list_projects` e `read_session` pelo MCP Inspector.
5. Faça um envio **não destrutivo** com `send_message`; confira se a mesma sessão recebeu a mensagem.
6. Configure o túnel e valide chamada real no ChatGPT.

Evite enviar mensagens de escrita ou conceder aprovações automáticas antes de completar esses gates.

- Empacotamento atualizado para `pyproject.toml`; removido `requirements.txt`. Exemplo para worktrees adicionado, mas ainda sem descoberta automática.
