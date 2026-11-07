# History agent

Code review harness for Svace warnings. Requires Python 3.11+, a model endpoint,
and a running history server.

## Installation

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Chat

```sh
.venv/bin/python chat.py
```

The first launch opens settings. Save them to reuse them on later launches.
To edit saved settings, run `.venv/bin/python chat.py --configure`.

For a one-off run with a local model:

```sh
.venv/bin/python chat.py \
  --provider llama \
  --model-url http://127.0.0.1:8000/v1 \
  --model qwen-local \
  --history-url http://127.0.0.1:8060
```

Explicit arguments override saved defaults without updating them.
Use `.venv/bin/python chat.py --help` for available options.

Set `HISTORY_USER` and `HISTORY_PASSWORD`, or `HISTORY_SESSION`, if the history
server requires authentication. Model API keys are read from environment
variables: `HISTORY_AGENT_MODEL_API_KEY` for compatible endpoints,
`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `OPENAI_API_KEY`.

## External clients

```sh
.venv/bin/python -m pip install -r requirements-setup.txt
.venv/bin/python configure.py \
  --client copilot \
  --model-url http://127.0.0.1:8000 \
  --history-url http://127.0.0.1:8060 \
  --model qwen-local
```

Supported clients: `copilot`, `opencode`, `openclaw`, `hermes`, `claude-code`,
and `codex`. The latter two require Messages and Responses compatible endpoints,
respectively. Keep this checkout at the configured path.
