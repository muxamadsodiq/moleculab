"""Provider-independent LLM adapter.

`get_provider()` returns None when no provider is configured; callers must then
work without generation and say so. Providers only turn a prompt into text:
retrieval, citation checking and validation stay in app.assistant.
"""
from __future__ import annotations

import os
from typing import Protocol

from app.config import settings


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> str: ...


class AnthropicProvider:
    """Claude via the official `anthropic` SDK (optional dependency: pip install anthropic)."""

    name = "anthropic"

    def __init__(self, api_key: str = "", model: str = ""):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("LLM_PROVIDER=anthropic but the 'anthropic' package is not installed") from exc
        self._anthropic = anthropic
        # Without an explicit key the SDK resolves ANTHROPIC_API_KEY / ant auth profiles itself.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model or "claude-opus-5-5"

    def complete(self, system: str, user: str) -> str:
        a = self._anthropic
        try:
            resp = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc
        if resp.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        return "".join(b.text for b in resp.content if b.type == "text")


class ClaudeCodeCLIProvider:
    """Generation through the locally installed, already-authenticated Claude Code CLI.

    This is a **local development bridge**, used when no application API key is available. It is real
    model generation - the CLI calls the real model - but it depends on the operator's own Claude Code
    installation and login, so it works only on a machine where that is present.

    **Do not enable this on a public deployment.** It spawns a local process per request. It is
    hardened here, but an API key with `LLM_PROVIDER=anthropic` is the correct production path:

    * every tool is disabled and the permission mode is read-only, so a prompt-injected instruction
      inside retrieved scientific text cannot cause file, shell or network actions;
    * arguments are passed as a list (never a shell string), so prompt text cannot be interpreted
      as shell syntax;
    * the prompt is length-capped and the subprocess is killed on timeout.

    Whatever this returns is still split into atomic claims and verified against cited records by
    app.verification, exactly like any other provider's output.
    """

    name = "claude_cli"

    #: Every tool the CLI might otherwise use. Disabled so generation cannot act on the machine.
    _BLOCKED_TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
                      "Task", "TodoWrite", "NotebookEdit", "Agent", "Artifact")
    MAX_PROMPT_CHARS = 60_000
    #: Must stay a single line (see complete()). It only sets the role; the caller's full
    #: instructions and the evidence records arrive in the message itself.
    ROLE_PROMPT = ("You are MOLECULAB's scientific answering engine. You are not a coding assistant "
                   "and have no project context. Use only the instructions and evidence records in "
                   "the message. Follow the output format it specifies exactly, with no preamble.")

    def __init__(self, model: str = "", timeout: float = 120.0, executable: str = ""):
        import shutil

        self._exe = executable or os.getenv("CLAUDE_CLI_PATH", "") or shutil.which("claude") or ""
        if not self._exe:
            raise LLMError(
                "LLM_PROVIDER=claude_cli but the 'claude' CLI was not found on PATH. Install Claude "
                "Code and sign in, or set CLAUDE_CLI_PATH.")
        self.model = model or "claude-opus-5-5"
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        import subprocess
        import tempfile

        prompt = user if len(user) <= self.MAX_PROMPT_CHARS else user[:self.MAX_PROMPT_CHARS]
        # The prompt goes in on STDIN, not as an argument: Windows rebuilds argv from a command
        # string and mangles embedded newlines, which silently truncated the prompt to its first
        # line. The role prompt passed as an argument is therefore kept to a single line, and the
        # full instructions travel with the message.
        stdin_text = f"{system}\n\n{prompt}\n"
        argv = [
            self._exe, "--print",
            "--model", self.model,
            "--permission-mode", "plan",        # read-only session
            # REPLACE the CLI's own coding-agent system prompt rather than appending to it. Appending
            # left it behaving as a coding assistant: it answered about the repository (citing source
            # files) instead of the supplied scientific records. Single line, for the argv reason above.
            "--system-prompt", self.ROLE_PROMPT,
            "--strict-mcp-config",              # ignore any configured MCP servers
            "--disallowedTools", *self._BLOCKED_TOOLS,
        ]
        try:
            # Run in an empty directory so the CLI cannot pick up this repository as project
            # context. Scientific answers must come only from the records in the prompt.
            with tempfile.TemporaryDirectory(prefix="moleculab-llm-") as sandbox:
                completed = subprocess.run(
                    argv, input=stdin_text, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=self.timeout, shell=False, cwd=sandbox,
                )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"local Claude Code CLI timed out after {self.timeout:.0f}s") from exc
        except OSError as exc:
            raise LLMError(f"could not run the local Claude Code CLI: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            raise LLMError(f"local Claude Code CLI failed (exit {completed.returncode}): "
                           f"{detail[-1] if detail else 'no error output'}")
        text = (completed.stdout or "").strip()
        if not text:
            raise LLMError("local Claude Code CLI returned no text")
        return text


def get_provider() -> LLMProvider | None:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings.llm_api_key, settings.llm_model)
    if settings.llm_provider == "claude_cli":
        return ClaudeCodeCLIProvider(settings.llm_model, settings.llm_cli_timeout)
    if settings.llm_provider in ("", "none"):
        return None
    raise LLMError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
