# Herdr Agent Bridge MCP — Windows

MCP **local** em Python que permite ao ChatGPT controlar sessões **Claude Code** e **Codex CLI** executadas no **Herdr Core (`hc`)**. Esta é uma nova implementação, independente da versão anterior baseada em `tmux`.

**Estado:** protótipo testável. O adaptador Herdr usa a CLI oficial, não a Socket API. Os testes automatizados usam uma simulação da CLI; **não** validam a execução real no Windows, no Herdr, em Claude/Codex nem no ChatGPT.

## Capacidades (8 ferramentas)

| MCP tool | Efeito |
|---|---|
| `list_projects()` | Lista projetos permitidos no `config.json` |
| `list_sessions()` | Lista somente sessões Herdr autorizadas, inclusive as finalizadas |
| `get_session(session)` | Retorna estado e status reportados pelo Herdr; `unknown` não significa `done` |
| `read_session(session, lines=60)` | Lê a tela/histórico até 200 linhas, limitadas a 24 mil caracteres |
| `start_agent(project, agent)` | Inicia Claude ou Codex persistente em projeto permitido, se habilitado |
| `send_message(session, message)` | Envia instrução via `hc send`, se o agente em primeiro plano for confirmado |
| `wait_session(session, seconds=8)` | Aguarda até 15 s por uma mudança; `timeout` é apenas um check-in |
| `interrupt_session(session)` | Pede interrupção do agente; **desabilitado por padrão** |

**Sem** ferramenta de shell genérico, `hc keys`, `hc kill`, `hc exec`, leitura arbitrária de arquivos, aprovações automáticas ou flag `hc send --force`.

## 1. Pré-requisitos no Windows

- Windows 10/11 **x64** com PowerShell e Python **3.11+** (recomendado 3.12).
- Herdr **Core** instalado e visível como `hc` no PATH. Herdr TUI é um runtime separado e não compartilha sessões.
- Claude Code e/ou Codex CLI instalados, autenticados e visíveis no PATH usado pelo Herdr.
- Para templates e comandos de shell específicos do Herdr no Windows, pode ser necessário `sh` do Git for Windows.
- Workspace/organização OpenAI com permissão para conectar um MCP privado por Secure MCP Tunnel.

Comando oficial para instalar o Herdr Core (revise antes de executar scripts remotos):

```powershell
powershell -c "irm https://releases.herdr.dev/core/install.ps1 | iex"
```

Verifique:

```powershell
hc --version
hc server status
hc ls --json
Get-Command hc, claude, codex -ErrorAction SilentlyContinue
```

**Nota sobre status:** Herdr Core **não analisa a tela** para adivinhar se o agente concluiu. `idle`, `working`, `done`, `blocked`, `error` dependem do programa emitir eventos **OSC 7501**. Sem esse suporte, o MCP mostra `unknown`. `blocked` exige intervenção humana; não contorne aprovações pela CLI.

## 2. Instalar o MCP (pyproject.toml)

Extraia o ZIP e abra PowerShell na pasta `herdr-agent-bridge-mcp`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.json config.json
notepad config.json
```

Se você só tiver outra versão 3.11+, use `py -3 -m venv .venv`.

Ajuste **obrigatoriamente** o caminho de exemplo `C:\dev\meu-projeto` para a pasta real de seu repositório. Em JSON, barras invertidas de Windows precisam ser escapadas: `C:\\dev\\repositorio`. Alternativamente, use `/`, como `C:/dev/repositorio`.

Exemplo resumido de `config.json`:

```json
{
  "hc_executable": "hc",
  "projects": {
    "harness": {
      "path": "C:/dev/hv-harness",
      "agents": ["claude", "codex"]
    }
  },
  "attached_sessions": {},
  "agent_commands": {"claude": ["claude"], "codex": ["codex"]},
  "allow_launch": true,
  "allow_send": true,
  "allow_interrupt": false
}
```

Se seu Windows instala os CLIs como `claude.cmd` ou `codex.cmd`, configure `agent_commands` com o executável reconhecido pelo seu `hc`. O `hc run` executa programas dentro da máquina local; **não** é um shell genérico desta integração.

### Repositórios com muitos Git worktrees

A versão atual **não descobre worktrees automaticamente**: cadastre cada worktree autorizado
como um projeto independente, com `path` apontando para a raiz daquele worktree.
Isso permite distinguir agentes que estão no mesmo repositório, mas em branches diferentes.

Use `config.worktrees.example.json` como modelo:

```powershell
Copy-Item config.worktrees.example.json config.json
notepad config.json
```

Substitua os caminhos pelos worktrees reais retornados por `git worktree list --porcelain`.
Use nomes únicos (`harness-main`, `harness-auth`, `harness-qa`) e associe sessões
existentes em `attached_sessions` apenas quando souber em qual worktree executam.
Não aponte todos os worktrees para a raiz de um mesmo repositório: o MCP confere o
`cwd` do terminal contra a raiz cadastrada antes de autorizar o acesso.
O arquivo local `config.json` fica fora do Git por `.gitignore`.

## 3. Criar ou associar sessões Herdr

### Criar uma sessão diretamente no Herdr (manual)

```powershell
hc run --bg --name meu-claude --cwd C:\dev\hv-harness claude
hc ls --json
hc get meu-claude --json
```

Para o MCP controlar esse terminal existente, inscreva-o **localmente** em `attached_sessions`:

```json
"attached_sessions": {
  "claude-harness": {
    "project": "harness",
    "terminal": "meu-claude",
    "agent": "claude"
  }
}
```

O MCP confere se `cwd` está dentro do projeto autorizado e se o Herdr realmente identifica o agente como `claude` antes de **enviar** mensagens. Quando o Herdr informa explicitamente que o processo em primeiro plano é PowerShell ou outro shell, a operação é bloqueada mesmo que haja um status antigo de agente. Se não confirmar a identidade, apenas leitura fica disponível; nenhum texto é enviado ao possível shell.

### Criar a sessão pelo MCP

Peça no ChatGPT `start_agent(project="harness", agent="codex")`. O MCP usa `hc run --bg --no-parent`, com `--cwd` e rótulos privados (`agent_bridge=yes`, `project=...`, `agent=...`). Esse tipo de sessão é reconhecido mesmo após reiniciar o MCP. Depois, use `list_sessions` e `get_session` para confirmar a identidade antes de enviar instruções.

Você também pode marcar localmente uma sessão existente em vez de cadastrá-la por alias:

```powershell
hc label meu-claude agent_bridge=yes project=harness agent=claude
```

**Importante:** marcação e path não são suficientes para autorizar envio a um `PowerShell` ou shell qualquer; é preciso o campo de identidade `agent` confirmado pelo Herdr. Nem toda instalação/versão dos CLIs fornece isso. Nesse caso, não afrouxe as verificações sem avaliar riscos.

## 4. Testar localmente

O teste da lógica exige apenas Python (não exige SDK MCP, hc ou Windows):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Checagem **real de leitura** do Herdr e do seu `config.json`:

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

Teste interativo MCP (requer Node.js / `npx` para o Inspector):

```powershell
.\.venv\Scripts\mcp.exe dev server.py
```

O servidor stdio em si é iniciado por:

```powershell
.\.venv\Scripts\python.exe server.py
```

Esse processo não mostra uma interface de chat; ele aguarda mensagens JSON-RPC pelo stdin. **Não** digite comandos manualmente nesse console.

## 5. Conectar ao ChatGPT via Secure MCP Tunnel

O ChatGPT web não acessa seu `localhost` diretamente; a OpenAI oferece o túnel privado de saída para ligar a máquina ao ChatGPT **sem liberar uma porta inbound**.

1. Em `https://platform.openai.com/settings/organization/tunnels`, crie um túnel associado à organização Platform e ao workspace ChatGPT corretos; guarde seu `tunnel_id`.
2. Instale `tunnel-client` a partir da release oficial `https://github.com/openai/tunnel-client/releases/latest`.
3. Configure a chave de runtime **no ambiente local**, sem salvá-la no repositório. Ela requer permissão Tunnels Read + Use. Para criar o túnel, é preciso Read + Manage.
4. No PowerShell, a partir da pasta do MCP, configure o perfil (ajuste o ID):

```powershell
$root = (Resolve-Path .).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$server = Join-Path $root 'server.py'
$mcpCommand = "`"$python`" `"$server`""

tunnel-client help quickstart

tunnel-client init --sample sample_mcp_stdio_local `
    --profile herdr-bridge `
    --tunnel-id tunnel_SEU_ID `
    --mcp-command $mcpCommand

tunnel-client doctor --profile herdr-bridge --explain
tunnel-client run --profile herdr-bridge
```

5. No **ChatGPT web**: Plugins → `+` → **Add custom MCP server** → Connection: **Tunnel** → selecione o mesmo túnel → configure autenticação, aceite o aviso de risco e crie o plugin.
6. Teste `list_projects`, `list_sessions`, `get_session`, `read_session` **antes** de usar `send_message`.

O Secure MCP Tunnel tem **limite padrão de 120 s por requisição**. Por isso o MCP retorna rapidamente após `send_message` e oferece `wait_session` com limite de 15 s. Não confunda aceitar a mensagem com concluir a tarefa.

## 6. Exemplo de uso no ChatGPT

> Use Herdr Agent Bridge. Liste meus projetos e sessões autorizadas. No projeto `harness`, localize o Claude Code. Leia a tela antes de qualquer comando. Peça uma revisão **sem alterações** do último commit, não autorize prompts de permissão e me diga quando ele sinalizar conclusão. Se o estado for unknown, informe que não há confirmação estruturada.

## Segurança e limites

- **O próprio agente pode executar ferramentas e editar arquivos.** `send_message` não é sandbox; mantenha as permissões e aprovações do Claude/Codex ativas.
- `config.json` é local e não deve ser versionado. Só seus projetos e sessões explicitamente inscritos são acessíveis. Validação de caminho usa a raiz real do projeto (inclui subdiretórios), não prefixo de texto. **Isto não é um sandbox**: o agente pode acessar outros diretórios conforme suas permissões locais.
- Se um agente sair e devolver o terminal a um shell, o Herdr pode reter status antigo, especialmente no Windows. O MCP bloqueia quando identifica um shell em primeiro plano, mas essa verificação é uma barreira de segurança parcial, não uma garantia contra condições de corrida ou identificação incorreta.
- Saída do terminal pode conter **segredos ou informações confidenciais**, que serão enviados ao ChatGPT quando lidos. Não exponha tokens ou dumps de credenciais no terminal.
- O MCP **não** descobre e controla automaticamente toda sessão de qualquer pasta; isso é deliberado.
- Sessões bloqueadas por permissão não recebem novas instruções; nenhuma aprovação automática ou `--force` é implementada.
- Na primeira versão, leitura da tela **não é streaming de eventos semânticos**, e status depende do OSC 7501.
- O Herdr Core e o Herdr TUI original **não compartilham sessões**.
- Reiniciar o Windows encerra os processos ativos; o Core não promete retomar trabalho em andamento automaticamente após reboot.
- **Não foi feita validação fim a fim neste pacote.** Valide em sua máquina com Herdr e CLIs reais, depois com o plugin no ChatGPT.

## Evoluções planejadas (não implementadas)

- Cliente persistente para a API do Core (`hc connect` / `hc schema`) e stream de eventos.
- Histórico incremental por cursor, para evitar repetir trechos da tela.
- Monitor de execução e Git com evidências por tarefa.
- Operações de aprovação humana com confirmação externa auditável (não por envio de teclas).
- Testes de integração reais no Windows.

## Referências oficiais

- Herdr Core: https://herdr.dev/core/
- CLI: https://github.com/herdrdev/herdr-core/blob/master/docs/commands.md
- Herdr FAQ: https://herdr.dev/faq
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Secure MCP Tunnel: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- Adicionar MCP ao ChatGPT: https://developers.openai.com/api/docs/guides/custom-mcp-server
