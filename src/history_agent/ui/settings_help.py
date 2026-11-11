"""Hover descriptions for the connection and review settings."""

SETTINGS_HELP: dict[str, str] = {
    "check-connections": (
        "Send short test requests to the configured main and System 1 "
        "models. Green square: connected; red square: unavailable or disabled."
    ),
    "system_one_url": "Full System 1 API URL, including /v1/systemone.",
    "system_one_model": (
        "Optional adviser, enabled only after a successful API probe. "
        "The agent asks it first: confidence >= 0.7 allows an immediate "
        "verdict if the agent agrees; lower scores guide investigation. "
        "Leave empty to disable."
    ),
    "provider": (
        "The model API protocol. For Qwen served by vLLM or LM Studio, "
        "choose openai-compatible; for llama-server, choose llama; "
        "for Ollama, choose ollama."
    ),
    "model_url": (
        "The inference server URL, usually ending in /v1. This service "
        "generates model responses. Enter the history server address "
        "separately below."
    ),
    "model": (
        "The model ID accepted by the inference server. For local servers, "
        "leave it empty to auto-detect when /models lists exactly one model."
    ),
    "history_url": (
        "The Svace code history server URL. The harness reads source code "
        "and warnings here and sends review results to this server."
    ),
    "history_user": (
        "Your history server username. Enter it together with the password. "
        "These credentials belong to the code history service."
    ),
    "history_password": (
        "Your history server password. Input is masked. Save and start "
        "stores it in the local configuration file with owner-only "
        "permissions (0600)."
    ),
    "model_key_env": (
        "The name of the environment variable containing the model API "
        "key, for example OPENAI_API_KEY. Enter the variable name here "
        "and set its value before launching. Local models often need no key."
    ),
    "allow_write": (
        "Checked: save each validated verdict to the server immediately, "
        "before reviewing the next warning. Unchecked: keep results in the "
        "local job without changing server reviews. Existing reviews are "
        "preserved unless overwriting is explicitly enabled."
    ),
    "max_steps": (
        "The maximum number of model requests for one chat turn or one "
        "warning investigation, including tool-calling steps. This limit "
        "does not control the number of warnings in a job."
    ),
    "max_output_tokens": (
        "The maximum output tokens per model request. A low limit may "
        "cut off reasoning or the final verdict."
    ),
    "max_context_bytes": (
        "The input context budget in JSON bytes, rather than model tokens. "
        "Includes instructions, messages and tool definitions. Each warning "
        "is investigated with a fresh context."
    ),
    "max_tool_result_bytes": (
        "The maximum size of one tool result in bytes. Oversized results "
        "may be omitted to protect the context budget. Request smaller "
        "pages or source windows when this limit is reached."
    ),
    "timeout": (
        "The model server response timeout in seconds. Increase it for "
        "slow models or long reasoning."
    ),
    "no_thinking": (
        "Checked: send enable_thinking=false for Qwen and compatible local "
        "models. This may speed up responses but reduce analysis quality. "
        "Unchecked: use the server's default reasoning mode."
    ),
    "no_stream": (
        "Checked: wait for the complete model response without incremental "
        "output. Use this for servers without SSE support. Unchecked: "
        "display the response as it is generated."
    ),
    "no_stream_usage": (
        "Checked: omit stream_options.include_usage. Use this if the model "
        "server rejects that option. Without reported token usage, the "
        "interface shows estimates marked with ~."
    ),
    "stats": (
        "Checked: show detailed request, timing, token and cache statistics. "
        "Basic speed and context metrics remain visible in the interface "
        "when this option is unchecked."
    ),
    "no_patterns": (
        "Checked: disable checklist reuse between warnings. Unchecked: "
        "reuse short investigation hints within the same error class, "
        "such as what to check before choosing TP or FP. These are "
        "hypotheses, not verdicts: each warning's code is verified again. "
        "Context is cleared between error classes."
    ),
    "save": "Save these settings for future launches and open the chat.",
    "cancel": "Close settings without saving changes.",
}

SETTINGS_HELP.update(
    {
        "model_profile": "Select a saved model or a manual connection.",
        "prompts_file": "JSON prompt overrides with validated placeholders.",
    }
)
