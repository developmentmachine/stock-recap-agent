"""Cursor CLI provider（subprocess 调用官方 ``agent`` 命令，stream-json）。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from stock_recap.config.settings import Settings
from stock_recap.domain.models import LlmError, LlmTokens, LlmTransportError, Mode, Recap
from stock_recap.infrastructure.llm.parse import _stable_json, parse_and_validate
from stock_recap.infrastructure.llm.providers._cli_shared import inject_prefetch

logger = logging.getLogger("stock_recap.infrastructure.llm.providers.cursor_cli")


class CursorCliProvider:
    name = "cursor-cli"

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
        base_cmd = settings.cursor_cli_cmd.strip().split()
        if not base_cmd:
            raise LlmError(
                "cursor-cli 命令为空，请设置 RECAP_CURSOR_CLI_CMD（或兼容项 RECAP_CURSOR_AGENT_CMD）"
            )

        msgs = inject_prefetch(messages, settings, db_path, date)
        prompt = _stable_json({"messages": msgs})
        cmd = (
            base_cmd
            + [
                "--print",
                "--output-format",
                "stream-json",
                "--stream-partial-output",
                "--trust",
                "--force",
                "--workspace",
                os.getcwd(),
            ]
            + [prompt]
        )

        t0 = time.time()
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        except Exception as e:
            raise LlmTransportError(f"cursor-cli 启动失败: {e}") from e

        stdout_lines: List[str] = []
        final_result_text: Optional[str] = None
        assistant_text_parts: List[str] = []
        last_log = 0.0

        while True:
            now = time.time()
            if now - last_log >= 5:
                last_log = now
                logger.info(_stable_json({"event": "cursor_cli_running", "elapsed_s": int(now - t0)}))

            if proc.poll() is not None:
                break

            if now - t0 > settings.cursor_timeout_s:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise LlmTransportError(f"cursor-cli 超时（>{settings.cursor_timeout_s}s）")

            got = False
            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    got = True
                    stdout_lines.append(line)
                    try:
                        evt = json.loads(line)
                        if isinstance(evt, dict):
                            if evt.get("type") == "result" and isinstance(evt.get("result"), str):
                                final_result_text = evt["result"]
                            if evt.get("type") == "assistant":
                                msg = evt.get("message")
                                if isinstance(msg, dict):
                                    for c in msg.get("content") or []:
                                        if isinstance(c, dict) and c.get("type") == "text":
                                            assistant_text_parts.append(c.get("text", ""))
                    except Exception:
                        pass
            if not got:
                time.sleep(0.2)

        rc = proc.returncode or 0
        if rc != 0:
            err_tail = "".join(stdout_lines).strip()[-800:]
            raise LlmTransportError(f"cursor-cli 失败(code={rc}): {err_tail}")

        raw = final_result_text or "".join(assistant_text_parts) or "".join(stdout_lines)
        if not raw.strip():
            raise LlmTransportError("cursor-cli 无输出")

        recap = parse_and_validate(raw.strip(), mode)
        return recap, LlmTokens()
