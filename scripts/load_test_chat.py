"""Load-tests the public chat request path (POST /v1/public/chat/sessions/{id}/messages) — the
one endpoint on the hot path for every real recruiter interaction, and the only one that chains
a DB write, an LLM call, and a vector-store query in sequence. Deliberately dependency-free
(asyncio + httpx, both already project dependencies) rather than pulling in locust/k6 for a
single-endpoint check.

Requires a published Chatfolio to chat against — pass its slug. Rate limiting (15 messages/min
per IP, Phase 11) means every simulated "session" is a different session id but all requests
still come from this one process's IP, so keep --sessions * --messages under that budget or
expect 429s to show up in the results (which is itself useful signal, not a bug in the tool).

Usage:
    python scripts/load_test_chat.py --slug my-chatfolio --sessions 5 --messages 2
    python scripts/load_test_chat.py --slug my-chatfolio --base-url http://localhost:8000
"""
import argparse
import asyncio
import time

import httpx


async def _run_session(client: httpx.AsyncClient, slug: str, messages: int) -> list[float]:
    latencies: list[float] = []
    start_resp = await client.post(f"/v1/public/chat/{slug}/sessions")
    start_resp.raise_for_status()
    session_id = start_resp.json()["session_id"]

    for i in range(messages):
        started = time.perf_counter()
        response = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": f"What is your experience with backend development? (msg {i})"},
        )
        latencies.append(time.perf_counter() - started)
        if response.status_code != 200:
            print(f"  session={session_id} message={i} -> HTTP {response.status_code}")
    return latencies


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * p), len(ordered) - 1)
    return ordered[index]


async def main(base_url: str, slug: str, sessions: int, messages: int) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        started = time.perf_counter()
        results = await asyncio.gather(
            *[_run_session(client, slug, messages) for _ in range(sessions)],
            return_exceptions=True,
        )
        total_elapsed = time.perf_counter() - started

    latencies: list[float] = []
    failures = 0
    for result in results:
        if isinstance(result, BaseException):
            failures += 1
            print(f"  session failed: {result!r}")
        else:
            latencies.extend(result)

    print(f"\n{sessions} sessions x {messages} messages = {sessions * messages} requests")
    print(f"failed sessions: {failures}")
    print(f"total wall time: {total_elapsed:.2f}s")
    if latencies:
        print(f"latency p50: {_percentile(latencies, 0.50) * 1000:.0f}ms")
        print(f"latency p95: {_percentile(latencies, 0.95) * 1000:.0f}ms")
        print(f"latency p99: {_percentile(latencies, 0.99) * 1000:.0f}ms")
        print(f"latency max: {max(latencies) * 1000:.0f}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--messages", type=int, default=2)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.slug, args.sessions, args.messages))
