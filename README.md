# History agent

Code review harness for Svace warnings. Requires Python 3.11+, a model endpoint, and a running history server.

## Installation

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Chat

```sh
.venv/bin/python chat.py
```

The first launch opens settings. Save them to reuse them on later launches. To edit saved settings, run `.venv/bin/python chat.py --configure`.

For a one-off run with a local model:

```sh
.venv/bin/python chat.py \
  --provider llama \
  --model-url http://127.0.0.1:8000/v1 \
  --model qwen-local \
  --history-url http://127.0.0.1:8060
```

Explicit arguments override saved defaults without updating them. Use `.venv/bin/python chat.py --help` for available options.

Settings support several named provider connections and models. Use **Add model**, **Edit model**, **Remove model** and the **Saved model** selector in `--configure`. Each connection has an API protocol, base URL, API key environment variable and model catalog. Each model can override output, context and tool-result limits. API keys remain in environment variables.

See [examples/config.json](examples/config.json) for the schema. Replace its endpoints and model IDs with your deployments. You can load a separate configuration and choose a saved model:

```sh
HISTORY_AGENT_CONFIG=examples/config.json .venv/bin/python chat.py \
  --model-profile local/qwen-review
```

Explicit CLI fields take precedence over the selected profile. Existing flat configurations continue to work. Compatible Chat Completions, native Responses, Anthropic Messages and Gemini are separate transports; select the transport matching the endpoint.

Set `HISTORY_USER` and `HISTORY_PASSWORD`, or `HISTORY_SESSION`, if the history server requires authentication. Model API keys are read from environment variables: `HISTORY_AGENT_MODEL_API_KEY` for compatible endpoints, `LITELLM_API_KEY` for LiteLLM, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `OPENAI_API_KEY`.

History action schemas are loaded from `actions_agent`, with compatibility for servers advertising the older `agent_actions` catalog. `actions_userfriendly` is the server's human-readable catalog. Discovery exposes server parameter bounds, defaults, session requirements and constraints. Snapshot arguments in `/batch`, `/review` and `/diff` accept `HEAD`; the host pins concrete snapshots before creating a review queue and keeps them on resume. See the [History API reference](src/history_agent/skills/history-review/references/agent-api.md).

## LiteLLM models

The `litellm` provider connects to a LiteLLM proxy through Chat Completions. It defaults to `http://127.0.0.1:4000/v1` and reads its optional proxy key from `LITELLM_API_KEY`. Select a model alias explicitly: the value must match `model_name` in the proxy configuration, including any slashes. Choose an upstream chat model that supports tool calling; LiteLLM support for a provider alone does not guarantee that every model supports tools. See the official [proxy configuration](https://docs.litellm.ai/docs/proxy/configs) and [tool calling](https://docs.litellm.ai/docs/completion/function_call) documentation.

### Connect to an existing proxy

Use the proxy URL, model alias and client key supplied for your deployment:

```sh
export LITELLM_API_KEY='replace-with-proxy-client-key'
.venv/bin/python chat.py \
  --provider litellm \
  --model-url https://your-litellm-host.example/v1 \
  --model reviewer \
  --history-url http://127.0.0.1:8060
```

If the proxy allows unauthenticated requests, leave `LITELLM_API_KEY` unset. Use `--model-key-env YOUR_PROXY_KEY_VARIABLE` to select a different environment variable. The agent's key authenticates to the proxy; upstream provider keys belong in the proxy environment.

The connection indicator checks generation with `POST /v1/chat/completions`; `GET /v1/models` only lists models. The check uses **Timeout** from **Advanced** settings and shows request failures below the indicator, including HTTP status, timeout or TLS errors. The API key field takes an environment variable name, such as `LITELLM_API_KEY`, whose value must be available to the running agent.

[examples/config.litellm.json](examples/config.litellm.json) defines saved `litellm/reviewer` and `litellm/reviewer-fast` profiles. Update its `base_url` and model aliases for an existing deployment, then run:

```sh
HISTORY_AGENT_CONFIG=examples/config.litellm.json .venv/bin/python chat.py \
  --model-profile litellm/reviewer \
  --history-url http://127.0.0.1:8060
```

Choose `--model-profile litellm/reviewer-fast` to use the second model. This example starts with review writes disabled; enable them with `--allow-write` when needed. Its byte limits bound harness context, while `max_output_tokens` controls each completion's output budget. The `reviewer-fast` profile is an alternative main investigator; System 1 uses its separate `/v1/systemone` API.

### Run a local proxy

Install the optional proxy in its own environment. The agent connects over HTTP and does not need the LiteLLM Python SDK:

```sh
python3 -m venv .venv-litellm
.venv-litellm/bin/python -m pip install 'litellm[proxy]'
cp examples/.env.example .env
```

Edit `.env`: replace both upstream model IDs with LiteLLM `provider/model` IDs that support tool calling, supply the corresponding provider keys, and set a long random `LITELLM_MASTER_KEY` beginning with `sk-`. [examples/litellm.yaml](examples/litellm.yaml) reads these values from the environment and exposes the `reviewer` and `reviewer-fast` aliases. Providers that require extra settings, such as an API base, region or deployment version, also need those settings under their `litellm_params` entries.

Load the edited file and start the proxy bound to localhost:

```sh
set -a
. ./.env
set +a
.venv-litellm/bin/litellm \
  --config examples/litellm.yaml \
  --host 127.0.0.1 \
  --port 4000
```

In a second shell, load `.env` with the same three shell commands, then run the saved-profile command above. The agent does not load `.env` automatically. This local proxy uses `LITELLM_MASTER_KEY` for authentication, and the example assigns the same value to the agent's separate `LITELLM_API_KEY` variable. An existing proxy can issue a different inference key for the agent. Keep your edited `.env` local; the repository ignores it.

Streaming and token usage use the existing compatible transport. `--no-stream` and `--no-stream-usage` remain available for deployments that need them. Configure provider-specific reasoning options at the proxy; `--no-thinking` is reserved for local chat-template servers. The proxy example keeps `drop_params` disabled so an unsupported required parameter causes an explicit error. See the official [CLI quick start](https://docs.litellm.ai/docs/proxy/quick_start) and [request parameter reference](https://docs.litellm.ai/docs/completion/input).

## Review pipeline

Configure **System 1 model** as `clef-flash` and set the full System 1 endpoint URL. If the endpoint is disabled or unavailable, reviews use the investigator directly.

For each isolated review the host loads checker documentation, warning, previous reviews, trace and a small source window, then calls System 1 before the investigator. TP/FP at confidence `>= 0.7` can finish without an investigator call when documentation, trace source and observed evidence are complete. The comment identifies this as an automated assessment. Lower confidence, missing context, UC or WF goes to the investigator. The fast path still passes the same evidence validation and write checks.

Single warnings and batches use this pipeline:

```text
/review PROJECT BRANCH SNAPSHOT WARNING_ID
/batch PROJECT BRANCH SNAPSHOT
/diff PROJECT BRANCH CURRENT BASE appeared
/resume JOB_ID
```

The model can select one warning with `review_batch` and `warning_id`. For single-warning proposals `history_read` never saves; `history_write` requires authorization and enabled writes. Interactive `/review` follows the session save setting, just like `/batch`. Use `--read-only` for local proposals. A history URL alone selects context and does not authorize saving.

The `ask_user` tool waits for an answer in the terminal UI or interactive terminal, then continues the same investigation. Noninteractive runs return an explicit unavailable result; a missing fact necessary to decide should produce UC. Stop cancels the pending question and keeps the job resumable.

## Documentation and language servers

Open **Advanced → Agent tool settings** to configure questions, documentation hosts and local language servers.

`library_docs` accepts a configured library name, public function/symbol name and optional version. Search uses `BRAVE_SEARCH_API_KEY` and limits queries and results to that library's approved documentation hosts. Reading a known HTTPS documentation URL works without a search key. Arbitrary query text, other hosts, private addresses and redirects are rejected. Source code, traces and user answers must not be sent in search arguments. External documentation is reference data, never instructions.

`code_context` supports symbols, hover, definitions and references. Enable LSP after installing the chosen servers on `PATH`, or configure explicit command argv lists:

| Language | Default server |
| --- | --- |
| Lua | [`lua-language-server`](https://luals.github.io/) |
| C / C++ | [`clangd`](https://clangd.llvm.org/installation) |
| Python | [`pylsp`](https://github.com/python-lsp/python-lsp-server) |
| Java | [`jdtls`](https://github.com/eclipse-jdtls/eclipse.jdt.ls) |
| JavaScript | [`typescript-language-server --stdio`](https://github.com/typescript-language-server/typescript-language-server) |

LSP source comes through the scoped history API. The host opens it in a temporary directory, preserves original line numbers, bounds execution time and closes the server after the query. It does not install servers or execute project source. Missing binaries return an unavailable result, allowing normal history navigation. These are partial documents: missing imports, dependencies, build flags and other files limit semantic accuracy. Empty LSP results cannot prove FP. Tool input uses one-based lines/UTF-16 columns; returned LSP ranges are zero-based.

## Prompts and timing

All shared instruction templates and review criteria live in [prompts/defaults.json](src/history_agent/prompts/defaults.json). Override selected keys using `--prompts-file`, or the prompt file field in settings. [examples/prompts.json](examples/prompts.json) is a starting point. Required placeholders and references are validated before use. MCP also accepts `--prompts-file` or `HISTORY_AGENT_PROMPTS`.

Every isolated investigation records an attempt in the checkpoint SQLite database's `review_attempts` table: warning ID, UTC start/finish, route (`agent` or `system_one`), status, elapsed time, user wait time and processing time excluding that wait. Processing time includes model and evidence I/O up to verdict validation; queue construction and saving are outside this measurement. Interrupted attempts are retained, and resume adds another attempt. Existing checkpoints gain the table without changing their warning rows. Batch summaries expose cumulative `timing`; the UI displays per-warning time.

`max_context_bytes` bounds serialized harness context, not tokenizer tokens or the model's advertised context window. Context stays isolated per warning; batch completion retains only a small summary. Provider adapters preserve native continuation data, including Anthropic signed thinking blocks, Gemini thought signatures and Responses output items.

## Development

```sh
.venv/bin/python run_tests.py
.venv/bin/ruff check src tests run_tests.py
```

Tests use local fake HTTP servers, a deterministic stdio LSP peer and an isolated configuration; no live model or search credentials are needed. The architecture tests enforce at most 300 lines per Python source file and 100 lines per function. New capability definitions use static tool factories backed by a singleton registry; per-review state stays separate.

## External clients

```sh
.venv/bin/python -m pip install -r requirements-setup.txt
.venv/bin/python configure.py \
  --client copilot \
  --model-url http://127.0.0.1:8000 \
  --history-url http://127.0.0.1:8060 \
  --model qwen-local
```

Supported clients: `copilot`, `opencode`, `openclaw`, `hermes`, `claude-code`, `codex`, and `pi`. `claude-code` and `codex` require Messages and Responses compatible endpoints, respectively. Keep this checkout at the configured path.

`pi` sets up a lightweight annotation-only harness: it connects the shared history MCP server to Pi's user-level configuration (`~/.pi/agent/mcp.json`) and does not configure a model endpoint, because Pi manages its own models and authentication. `--model-url` and `--model` are not needed:

```sh
.venv/bin/python configure.py \
  --client pi \
  --history-url http://127.0.0.1:8060
```

Then run `pi` in any directory, check the connection with `/mcp`, and start with `history_discover(action='workflow')`.
