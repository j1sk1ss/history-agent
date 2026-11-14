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

## External clients

```sh
.venv/bin/python -m pip install -r requirements-setup.txt
.venv/bin/python configure.py \
  --client copilot
```

Supported clients: `copilot`, `opencode`, `openclaw`, `hermes`, `claude-code`, `codex`, and `pi`. `claude-code` and `codex` require Messages and Responses compatible endpoints, respectively. Keep this checkout at the configured path.

Then run in any directory, check the connection with `/mcp`, and start with `history_discover(action='workflow')`.
