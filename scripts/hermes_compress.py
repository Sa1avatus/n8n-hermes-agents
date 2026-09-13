#!/usr/bin/env python3

import asyncio
import base64
import json
import sys
import uuid
from typing import Any
from urllib.parse import quote

import websockets


DEFAULT_WS_BASE = "ws://host.docker.internal:9119/api/ws"
DEFAULT_TIMEOUT = 120.0
DEFAULT_KEEP_RECENT = 20


def emit(result: dict[str, Any]) -> None:
    print(
        json.dumps(result, ensure_ascii=False),
        flush=True,
    )


def fail(
    message: str,
    details: Any = None,
) -> None:
    result = {
        "status": "failed",
        "compression_failed": True,
        "compression_completed": False,
        "error": message,
    }

    if details is not None:
        result["details"] = details

    emit(result)


def decode_input() -> dict[str, Any]:
    if len(sys.argv) < 2:
        fail("Missing input payload.")
        return {}

    token = str(sys.argv[1]).strip()

    # Убираем случайные внешние кавычки shell/n8n.
    if (
        len(token) >= 2
        and token[0] == token[-1]
        and token[0] in {"'", '"'}
    ):
        token = token[1:-1].strip()

    candidates: list[tuple[str, bytes]] = []

    # ------------------------------------------------------------
    # 1. Raw JSON
    # ------------------------------------------------------------
    if token.startswith("{"):
        candidates.append(
            ("raw", token.encode("utf-8"))
        )

    # ------------------------------------------------------------
    # 2. Hex
    # ------------------------------------------------------------
    if token and len(token) % 2 == 0:
        try:
            candidates.append(
                ("hex", bytes.fromhex(token))
            )
        except ValueError:
            pass

    # ------------------------------------------------------------
    # 3. Base64 / URL-safe Base64
    # ------------------------------------------------------------
    try:
        normalized = "".join(token.split())

        padded = normalized + (
            "=" * (-len(normalized) % 4)
        )

        candidates.append(
            (
                "base64",
                base64.b64decode(
                    padded,
                    altchars=b"-_",
                    validate=False,
                ),
            )
        )
    except Exception:
        pass

    errors: list[str] = []

    for encoding, raw in candidates:
        try:
            value = json.loads(
                raw.decode("utf-8")
            )

            if isinstance(value, dict):
                return value

            errors.append(
                f"{encoding}: decoded JSON is not an object"
            )

        except Exception as exc:
            errors.append(
                f"{encoding}: {exc}"
            )

    fail(
        "Invalid input payload.",
        {
            "arg_length": len(token),
            "arg_prefix": token[:128],
            "attempts": errors,
        },
    )

    return {}


async def wait_for_result(
    ws,
    request_id: str,
    timeout: float,
) -> dict[str, Any]:

    deadline = (
        asyncio.get_running_loop().time()
        + timeout
    )

    while True:
        remaining = (
            deadline
            - asyncio.get_running_loop().time()
        )

        if remaining <= 0:
            raise TimeoutError(
                "Timeout waiting for JSON-RPC "
                f"response id={request_id}"
            )

        raw = await asyncio.wait_for(
            ws.recv(),
            timeout=remaining,
        )

        if isinstance(raw, bytes):
            raw = raw.decode(
                "utf-8",
                errors="replace",
            )

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if str(message.get("id", "")) != request_id:
            continue

        return message


def rpc_error(
    message: dict[str, Any],
) -> str:

    error = message.get("error")

    if not error:
        return ""

    if isinstance(error, str):
        return error

    if isinstance(error, dict):
        return str(
            error.get("message")
            or error.get("detail")
            or error.get("code")
            or json.dumps(
                error,
                ensure_ascii=False,
            )
        )

    return str(error)


async def compress_session(
    session_id: str,
    ticket: str,
    keep_recent: int,
    timeout: float,
    ws_base: str,
) -> dict[str, Any]:

    if not session_id:
        raise ValueError(
            "session_id is required."
        )

    if not ticket:
        raise ValueError(
            "ticket is required."
        )

    if keep_recent < 1:
        raise ValueError(
            "keep_recent must be >= 1."
        )

    ws_url = (
        f"{ws_base}"
        f"?ticket={quote(ticket, safe='')}"
    )

    async with websockets.connect(
        ws_url,
        open_timeout=15,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=None,
    ) as ws:

        # ============================================================
        # 1. RESUME EXISTING SESSION
        # ============================================================

        resume_id = str(uuid.uuid4())

        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": resume_id,
                    "method": "session.resume",
                    "params": {
                        "session_id": session_id,
                    },
                },
                ensure_ascii=False,
            )
        )

        resume_response = await wait_for_result(
            ws,
            resume_id,
            timeout,
        )

        resume_error = rpc_error(
            resume_response
        )

        if resume_error:
            raise RuntimeError(
                "session.resume failed: "
                + resume_error
            )

        resume_result = (
            resume_response.get("result")
        )

        if not isinstance(
            resume_result,
            dict,
        ):
            raise RuntimeError(
                "session.resume returned invalid "
                "result: "
                + json.dumps(
                    resume_response,
                    ensure_ascii=False,
                )
            )

        # ------------------------------------------------------------
        # Hermes semantics:
        #
        # Input:
        #   session.resume(session_id=<stored run_...>)
        #
        # Result:
        #   result.session_id = runtime session ID
        #   result.resumed    = original run_... ID
        #
        # session.compress must use result.session_id.
        # ------------------------------------------------------------

        runtime_session_id = str(
            resume_result.get("session_id")
            or ""
        ).strip()

        resumed_original_id = str(
            resume_result.get("resumed")
            or ""
        ).strip()

        if not runtime_session_id:
            raise RuntimeError(
                "session.resume returned no runtime "
                "session_id: "
                + json.dumps(
                    resume_response,
                    ensure_ascii=False,
                )
            )

        # Если Hermes вернул исходный session ID,
        # проверяем, что он соответствует тому,
        # который мы передавали.
        if (
            resumed_original_id
            and resumed_original_id != session_id
        ):
            raise RuntimeError(
                "session.resume resumed a different "
                "session: "
                f"expected={session_id} "
                f"actual={resumed_original_id}"
            )

        # ============================================================
        # 2. COMPRESS RUNTIME SESSION
        # ============================================================

        compress_id = str(uuid.uuid4())

        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": compress_id,
                    "method": "session.compress",
                    "params": {
                        "session_id": runtime_session_id,
                        "keep_recent": keep_recent,
                    },
                },
                ensure_ascii=False,
            )
        )

        compress_response = await wait_for_result(
            ws,
            compress_id,
            timeout,
        )

        compress_error = rpc_error(
            compress_response
        )

        if compress_error:
            raise RuntimeError(
                "session.compress failed: "
                + compress_error
            )

        result = (
            compress_response.get("result")
            or {}
        )

        if not isinstance(result, dict):
            result = {
                "value": result
            }

        status = str(
            result.get("status")
            or result.get("commit_status")
            or "completed"
        ).lower()

        commit_status = str(
            result.get("commit_status")
            or ""
        ).lower()

        failed = (
            status
            in {
                "failed",
                "aborted",
                "cancelled",
                "canceled",
            }
            or commit_status
            in {
                "failed",
                "aborted",
            }
        )

        completed = (
            status == "completed"
            or commit_status == "committed"
        )

        # Для внешнего state manager сохраняем
        # исходный session ID run_..., а не runtime ID.
        return {
            "status": status,
            "commit_status": commit_status,
            "compression_failed": failed,
            "compression_completed": (
                completed and not failed
            ),

            # Оригинальная Hermes session.
            "session_id": session_id,

            # Runtime session, который использовался
            # для session.compress.
            "runtime_session_id": runtime_session_id,

            # Для диагностики.
            "resumed": resumed_original_id,

            "output": result,
        }


async def main() -> None:

    payload = decode_input()

    if not payload:
        return

    session_id = str(
        payload.get("session_id")
        or ""
    ).strip()

    ticket = str(
        payload.get("ticket")
        or ""
    ).strip()

    keep_recent = int(
        payload.get(
            "keep_recent",
            DEFAULT_KEEP_RECENT,
        )
    )

    timeout = float(
        payload.get(
            "timeout",
            DEFAULT_TIMEOUT,
        )
    )

    ws_base = str(
        payload.get("ws_base")
        or DEFAULT_WS_BASE
    ).strip()

    try:

        result = await compress_session(
            session_id=session_id,
            ticket=ticket,
            keep_recent=keep_recent,
            timeout=timeout,
            ws_base=ws_base,
        )

        emit(result)

    except Exception as exc:

        fail(
            str(exc),
            {
                "session_id": session_id,
                "ws_base": ws_base,
            },
        )


if __name__ == "__main__":
    asyncio.run(main())