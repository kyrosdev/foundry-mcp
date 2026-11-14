# Foundry MCP

Foundry é um servidor MCP local para trabalhar em projetos Roblox com Codex, Claude e outros clientes stdio. Ele combina o servidor integrado ao Roblox Studio com ferramentas para inspecionar, editar, testar e coordenar mudanças no place aberto.

No Windows, a entrada Rust faz a descoberta do catálogo enquanto o servidor Python inicia em paralelo. A disponibilidade das ferramentas nativas acompanha a versão instalada do Studio. O modo individual é o padrão.

## Funcionalidades

| Área | Recursos |
| --- | --- |
| Cena | Hierarquia, seleção, propriedades tipadas, tags, atributos, alterações em lote e exportação de modelos. |
| Luau | Fontes do editor, referências, mapa de impacto, diffs, formatação StyLua, lint Selene e análise Luau LSP com tipos Roblox. |
| Planos | Preparar, revisar, aplicar e reverter mudanças combinando scripts e instâncias, com verificação de conflitos e validação em cópia local. |
| Sync | Exportar scripts e mapa Rojo, preparar sincronização bidirecional e acompanhar alterações. Conflitos pausam o sync. |
| Playtests | Cenários versionados com asserções, logs e capturas, simulação de jogadores e dispositivos e gravação e repetição de bugs. |
| Equipe | Identidades de agentes, tarefas, bloqueios por caminho, mensagens, contexto compartilhado e evidências para revisão. |
| Monitor | Atividade em tempo real em um app Qt, cursores por agente no Studio e atalhos para alvos e resultados de playtest. |
| Open Cloud | Analytics, versões, publicação, assets, passes, produtos e DataStore, conforme permissões e escopos disponíveis. |
| Informação atual | Acompanhar sinais e estado do Studio e consultar fontes públicas na web. |

O cliente descobre as ferramentas via `tools/list`. Para ver os parâmetros e a descrição de uma ferramenta, chame `extra_help` com seu nome exato. Consulte o [guia de ferramentas](enhanced/HELP.md) para exemplos.

## Instalação no Windows x64

1. Instale o Roblox Studio atualizado e o Python 3.10 ou mais recente. Confirme com `python --version` no PowerShell.
2. Baixe **Foundry-MCP-1.0.9-windows-x64.zip** na [release v1.0.9](../../releases/tag/v1.0.9). Extraia a pasta inteira. Mantenha o executável junto dos demais arquivos em `enhanced`.
3. Abra um place no Studio. Em **Assistant → … → Manage MCP Servers**, ative **Enable Studio as MCP server**. [Guia do Roblox](https://create.roblox.com/docs/studio/mcp)
4. Escolha uma pasta local existente para o projeto. Substitua `C:\Tools\Foundry-MCP` e `C:\Projects\MeuJogo` pelos seus caminhos nos exemplos.
5. Configure um cliente abaixo e reinicie-o.
6. Peça ao assistente para rodar `list_roblox_studios` e depois `extra_help` com `{"tool":"extra_impact"}`. Se houver várias janelas, indique qual place deseja usar.

### Codex

~~~powershell
codex mcp add Foundry_MCP -- "C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe" --workspace "C:\Projects\MeuJogo" --session-label Codex
codex mcp list
~~~

Para configurar pelo arquivo `config.toml` do Codex, adicione:

~~~toml
[mcp_servers.Foundry_MCP]
command = 'C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe'
args = ['--workspace', 'C:\Projects\MeuJogo', '--session-label', 'Codex']
~~~

### Claude Code

~~~powershell
claude mcp add --transport stdio --scope user Foundry_MCP -- "C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe" --workspace "C:\Projects\MeuJogo" --session-label "Claude Code"
claude mcp list
~~~

### Claude Desktop

Em **Settings → Developer → Edit Config**, adicione a entrada abaixo ao objeto `mcpServers` existente. Preserve as outras entradas.

~~~json
{
  "mcpServers": {
    "Foundry_MCP": {
      "command": "C:\\Tools\\Foundry-MCP\\enhanced\\foundry-bootstrap.exe",
      "args": ["--workspace", "C:\\Projects\\MeuJogo", "--session-label", "Claude Desktop"]
    }
  }
}
~~~

### Usar Python diretamente

Em clientes stdio, também é possível configurar `python.exe` como comando. Passe o caminho absoluto de `enhanced/server.py` como primeiro argumento, seguido de `--workspace` e `--session-label`. Se o launcher Rust precisar de um Python fora do PATH, configure `FOUNDRY_PYTHON` com o caminho absoluto do interpretador.

### Abrir o monitor

Dê dois cliques em **Abrir Monitor.cmd**. O atalho instala Qt via pip caso necessário. Para iniciar pelo terminal na pasta extraída:

~~~powershell
python -m pip install -r enhanced/monitor-requirements.txt
python enhanced/monitor.py
~~~

O monitor e os clientes usam a mesma pasta de dados, por padrão `%LOCALAPPDATA%\StudioMCPExtra`. Se configurar `--data-dir` no servidor, passe o mesmo valor ao monitor. Use **Hide cursors** para ocultar os cursores. Os argumentos `--no-cursors` e `--no-monitor` controlam a visualização pelo servidor.

## Primeiro workflow

1. Selecione a janela do Studio com `list_roblox_studios`.
2. Rode `extra_impact` com um caminho como `["ServerScriptService","Config"]`. Use `include_source: true` para obter a fonte e o hash da mesma captura.
3. Use `extra_mixed_changes` para preparar o plano e revisar o diff.
4. Aplique o plano e rode um cenário com `extra_scenario`. Use o rollback do plano se precisar restaurar a edição.
5. Ative o modo equipe quando mais de um agente trabalhar no mesmo projeto. Para compartilhar locks e histórico local, configure a mesma pasta de dados.

## Permissões e dados

- O histórico, os planos, os locks e a atividade do monitor são armazenados localmente. As informações recebidas pelo cliente seguem as regras de dados do provedor de IA.
- A maioria das operações de editor usa a sessão do Studio. Algumas operações Open Cloud usam permissões concedidas no Studio, e certos fluxos REST aceitam `STUDIO_MCP_EXTRA_ROBLOX_API_KEY` no ambiente. [Permissões do Studio](https://create.roblox.com/docs/studio/mcp)
- O monitor exibe chamadas observáveis e agentes registrados. Alguns controles da barra e fluxos de publicação exigem uma janela interativa do Studio. A identificação por OCR na barra pode usar Windows OCR e FFmpeg.
- O mapa de impacto faz análise estática. Referências dinâmicas e comportamento de runtime requerem inspeção ou playtest.

## Desenvolvimento

O pacote da release inclui os binários Windows. Para reconstruir o launcher com Rust instalado, rode na raiz:

~~~powershell
python enhanced/generate_workbench_catalog.py
cargo build --locked --release --manifest-path enhanced/bootstrap/Cargo.toml
Copy-Item enhanced/bootstrap/target/release/foundry-bootstrap.exe enhanced/foundry-bootstrap.exe
python -m unittest discover -s tests
~~~

O Foundry é um projeto independente baseado no [servidor MCP open source da Roblox](https://github.com/Roblox/studio-rust-mcp-server). Consulte a [licença MIT](LICENSE) e os [avisos de terceiros](enhanced/THIRD-PARTY.md).
