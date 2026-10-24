"""The sweep loop and process_many, against a fake processor (no database, no Redis)."""

import asyncio
import uuid

import pytest

from app.modules.whatsapp.processor import InboundProcessor, run_sweeper


class FlakySweeps:
    """sweep() fails once, works once, then the app shuts down (task cancelled)."""

    def __init__(self) -> None:
        self.calls = 0

    async def sweep(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("database unreachable")
        if self.calls == 3:
            raise asyncio.CancelledError
        return 0


async def test_sweeper_keeps_going_after_a_failed_round_and_stops_when_cancelled() -> None:
    processor = FlakySweeps()

    with pytest.raises(asyncio.CancelledError):
        await run_sweeper(processor, 0)  # type: ignore[arg-type]

    assert processor.calls == 3


async def test_process_many_carries_on_after_an_error() -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    seen: list[uuid.UUID] = []

    class Processor(InboundProcessor):
        async def process(self, message_id: uuid.UUID) -> bool:
            seen.append(message_id)
            if message_id == ids[0]:
                raise ConnectionError("redis down")
            return message_id == ids[1]

    handled = await Processor(None, None, None).process_many(ids)  # type: ignore[arg-type]

    assert seen == ids
    assert handled == 1
