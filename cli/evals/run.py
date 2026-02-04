"""Run LLM eval prompts through the Cueso backend via WebSocket.

Usage:
    uv run python -m evals          # run all evals
    uv run python -m evals 1 3 6    # run specific evals by number
    uv run python -m evals --list   # list available evals
    uv run python -m evals --url ws://host:port/ws/chat  # custom server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets

EVALS: list[dict[str, str]] = [
    {
        "prompt": "Play that Rick and Morty episode with the snakes",
        "description": "Specific episode by description (requires research)",
    },
    {
        "prompt": "Play Inception on my Roku",
        "description": "Simple movie request",
    },
    {
        "prompt": "Can you put on The Bear?",
        "description": "Show by name (should find on Hulu)",
    },
    {
        "prompt": "Play Stranger Things season 4 episode 1",
        "description": "Specific season and episode",
    },
    {
        "prompt": "Play the episode of Breaking Bad where they blow up the lab",
        "description": "Ambiguous episode request (requires research)",
    },
    {
        "prompt": "Put on Severance season 2",
        "description": "Recent show on Apple TV+",
    },
]

DEFAULT_URL = "ws://localhost:8483/ws/chat"


async def run_eval(ws_url: str, prompt: str, eval_num: int) -> bool:
    """Run a single eval prompt. Returns True if content was found on a streaming service."""
    print(f"\n{'=' * 70}")
    print(f"EVAL {eval_num}: {prompt}")
    print(f"{'=' * 70}")

    success = False
    try:
        async with websockets.connect(ws_url, close_timeout=30) as ws:
            await ws.send(json.dumps({"message": prompt}))

            content_parts: list[str] = []
            tool_calls_seen: list[str] = []
            final_seen = False

            async for raw_msg in ws:
                event = json.loads(raw_msg)
                event_type = event.get("type", "unknown")

                if event_type == "session_created":
                    print(f"  [session: {event.get('session_id', '?')[:8]}...]")

                elif event_type == "content_delta":
                    delta = event.get("content", "")
                    content_parts.append(delta)
                    print(delta, end="", flush=True)

                elif event_type == "tool_call_delta":
                    tc = event.get("tool_call", {})
                    tool_name = tc.get("name", "")
                    if tool_name and tool_name not in tool_calls_seen:
                        tool_calls_seen.append(tool_name)
                        print(f"\n  [TOOL: {tool_name}]")

                elif event_type == "tool_result":
                    tool_name = event.get("tool_name", "")
                    result_text = event.get("result", "")

                    # Check find_content results for matches
                    if tool_name == "find_content":
                        try:
                            parsed = json.loads(result_text)
                            matches = parsed.get("matches", [])
                            if matches:
                                success = True
                                services = [m["service_name"] for m in matches]
                                print(f"  [FOUND on {', '.join(services)}]")
                            else:
                                print(f"  [NO MATCHES: {parsed.get('message', '')}]")
                        except json.JSONDecodeError:
                            preview = result_text[:200].replace("\n", " ")
                            print(f"  [TOOL RESULT: {preview}]")

                    elif tool_name == "launch_on_roku":
                        try:
                            parsed = json.loads(result_text)
                            launched = parsed.get("success", False)
                            msg = parsed.get("message", "")
                            # Count as success even if Roku is unreachable
                            # (content was found, just can't reach the device)
                            if launched or "connection failed" in msg.lower():
                                success = True
                            status = "OK" if launched else "FAIL"
                            print(f"  [LAUNCH {status}: {msg}]")
                        except json.JSONDecodeError:
                            preview = result_text[:200].replace("\n", " ")
                            print(f"  [TOOL RESULT: {preview}]")

                    else:
                        preview = result_text[:300].replace("\n", " ")
                        print(f"  [TOOL RESULT ({tool_name}): {preview}...]")

                elif event_type == "final":
                    final_content = event.get("content", "")
                    iterations = event.get("iteration_count", "?")
                    if final_content and not content_parts:
                        print(f"\n  [FINAL]: {final_content[:300]}")
                    print(f"\n  [iterations: {iterations}]")
                    final_seen = True
                    break

                elif event_type == "error":
                    print(f"\n  [ERROR]: {event.get('message', 'unknown error')}")
                    final_seen = True
                    break

            if not final_seen:
                print("\n  [WARNING: Connection closed without final event]")

            status = "PASS" if success else "FAIL"
            print(f"  [{status}] Tools used: {tool_calls_seen}")

    except Exception as e:
        print(f"\n  [EXCEPTION]: {e}")

    return success


async def run_all(ws_url: str, eval_indices: list[int]) -> int:
    """Run selected evals and return exit code (0 = all passed)."""
    print("Cueso Eval Suite")
    print(f"Backend: {ws_url}")
    print(f"Running: {len(eval_indices)} eval(s)\n")

    results: list[tuple[int, str, bool]] = []

    for idx in eval_indices:
        ev = EVALS[idx]
        passed = await run_eval(ws_url, ev["prompt"], idx + 1)
        results.append((idx + 1, ev["prompt"], passed))

    # Summary
    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    for num, prompt, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {num}. {prompt}")

    passed_count = sum(1 for _, _, p in results if p)
    total = len(results)
    print(f"\n  {passed_count}/{total} passed")

    return 0 if passed_count == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="evals",
        description="Run Cueso LLM eval prompts against the backend.",
    )
    parser.add_argument(
        "evals",
        nargs="*",
        type=int,
        metavar="N",
        help="Eval numbers to run (1-indexed). Omit to run all.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"WebSocket URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_evals",
        help="List available evals and exit.",
    )
    args = parser.parse_args()

    if args.list_evals:
        print("Available evals:")
        for i, ev in enumerate(EVALS, 1):
            print(f"  {i}. {ev['prompt']}")
            print(f"     {ev['description']}")
        sys.exit(0)

    if args.evals:
        indices = []
        for n in args.evals:
            if n < 1 or n > len(EVALS):
                print(f"Error: eval {n} out of range (1-{len(EVALS)})", file=sys.stderr)
                sys.exit(2)
            indices.append(n - 1)
    else:
        indices = list(range(len(EVALS)))

    exit_code = asyncio.run(run_all(args.url, indices))
    sys.exit(exit_code)
