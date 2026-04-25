"""Gemini CLI provider（subprocess 调用 ``gemini`` 命令）。"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Dict, List, Tuple

from agent_platform.config.settings import Settings
from agent_platform.domain.models import LlmError, LlmTokens, LlmTransportError, Mode, Recap
from agent_platform.infrastructure.llm.parse import _stable_json, parse_and_validate
from agent_platform.infrastructure.llm.providers._cli_shared import inject_prefetch

logger = logging.getLogger("agent_platform.infrastructure.llm.providers.gemini_cli")


class GeminiCliProvider:
    name = "gemini-cli"

    def call(
        self,
        settings: Settings,
        mode: Mode,
        messages: List[Dict[str, str]],
        *,
        model: str,
        db_path: str,
        date: str,
    ) -> Tuple[Recap, LlmTokens]:
        base_cmd = settings.gemini_cli_cmd.strip().split()
        if not base_cmd:
            raise LlmError("gemini-cli 命令为空，请设置 RECAP_GEMINI_CLI_CMD")

        msgs = inject_prefetch(messages, settings, db_path, date)

        prompt_parts: List[str] = []
        for msg in msgs:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"[System]\n{content}")
            else:
                prompt_parts.append(content)
        prompt = "\n\n".join(prompt_parts)

        env = os.environ.copy()
        if settings.gemini_api_key:
            env["GEMINI_API_KEY"] = settings.gemini_api_key

        cmd = base_cmd + ["-p", prompt]

        t0 = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except Exception as e:
            raise LlmTransportError(f"gemini-cli 启动失败: {e}") from e

        stdout_lines: List[str] = []
        last_log = 0.0

        while True:
            now = time.time()
            if now - last_log >= 5:
                last_log = now
                logger.info(_stable_json({"event": "gemini_running", "elapsed_s": int(now - t0)}))

            if proc.poll() is not None:
                break

            if now - t0 > settings.gemini_timeout_s:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise LlmTransportError(f"gemini-cli 超时（>{settings.gemini_timeout_s}s）")

            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    stdout_lines.append(line)
                else:
                    time.sleep(0.2)

        rc = proc.returncode or 0
        if rc != 0:
            stderr_out = ""
            if proc.stderr:
                stderr_out = proc.stderr.read()[-500:]
            raise LlmTransportError(f"gemini-cli 失败(code={rc}): {stderr_out}")

        raw = "".join(stdout_lines).strip()
        if not raw:
            raise LlmTransportError("gemini-cli 无输出")

        recap = parse_and_validate(raw, mode)
        return recap, LlmTokens()
