"""Interrupt manager — handles conversation interruption signals."""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("butler.interrupt")


class InterruptManager:
    """
    Manages interrupt signals per device.
    Allows clean cancellation of TTS generation and LLM streaming.
    """

    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}

    def register(self, device_id: str):
        """Register a device for interrupt handling."""
        self._events[device_id] = asyncio.Event()

    def unregister(self, device_id: str):
        """Remove a device's interrupt handler."""
        self._events.pop(device_id, None)

    def trigger(self, device_id: str):
        """Trigger interrupt for a device. All watchers will be notified."""
        if event := self._events.get(device_id):
            event.set()
            logger.debug(f"[{device_id}] Interrupt triggered")

    def clear(self, device_id: str):
        """Clear interrupt signal for a device."""
        if event := self._events.get(device_id):
            event.clear()
            logger.debug(f"[{device_id}] Interrupt cleared")

    async def wait(self, device_id: str, timeout: float = None) -> bool:
        """
        Wait for interrupt. Returns True if interrupted, False if timeout.
        Used by TTS synthesizer and LLM streamer to check for cancellation.
        """
        if event := self._events.get(device_id):
            try:
                await asyncio.wait_for(event.wait(), timeout)
                return True
            except asyncio.TimeoutError:
                return False
        return False

    def is_set(self, device_id: str) -> bool:
        """Check if interrupt is currently set (non-blocking)."""
        if event := self._events.get(device_id):
            return event.is_set()
        return False
