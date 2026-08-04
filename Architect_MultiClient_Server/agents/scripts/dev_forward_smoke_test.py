"""Manual smoke test for RecordForwarder against a running record-service
(e.g. scripts/dev_server_with_fakes.py on the other end) -- sends a
SessionStart + a few PCM chunks + a dropped-frames notice, then closes
gracefully, exercising the exact same code path event_handlers.py drives.

Usage: RECORD_SERVICE_GRPC_ADDR=127.0.0.1:50099 python scripts/dev_forward_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from src.services.record_service_client import RecordServiceClient  # noqa: E402


async def main() -> None:
    client = RecordServiceClient.get_instance()
    forwarder = await client.new_forwarder(
        room_id="smoke-room",
        track_id="smoke-track",
        participant_identity="smoke-user",
        source="mic",
        sample_rate=16000,
        channels=1,
    )
    if forwarder is None:
        print("FAIL: new_forwarder returned None")
        sys.exit(1)

    forwarder.send_audio(b"hello-" * 50)
    forwarder.send_audio(b"world-" * 50)
    await asyncio.sleep(0.2)  # let the writer/reader loops flush
    await forwarder.close()
    await client.close()

    print(f"OK: object_key={forwarder.object_key}")


if __name__ == "__main__":
    asyncio.run(main())
