#!/usr/bin/env python3
"""Full pipeline WebSocket test with detailed error tracing."""
import asyncio
import json
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(__file__))

# Test the EXACT code path the server uses for text input
from butler.config import config
from butler.session import DeviceSession, DeviceState, SessionManager, ConversationTurn
from butler.vad import VADProcessor
from butler.stt import StreamingSTT
from butler.llm import LLMStreamer
from butler.tts import TTSEngine
from butler.router import IntentRouter
from butler.interrupt import InterruptManager
from butler.orchestrator import ConversationOrchestrator

class FakeWebSocket:
    """Mock WebSocket to test orchestrator without actual connection."""
    def __init__(self):
        self.sent = []
    async def send_json(self, data):
        self.sent.append(("json", data))
        print(f"  SEND JSON: {data.get('type')} {data.get('text', data.get('state', ''))}")
    async def send_bytes(self, data):
        self.sent.append(("audio", len(data)))
        print(f"  SEND AUDIO: {len(data)} bytes")

async def test():
    print("=" * 50)
    print("Testing full orchestrator pipeline...")
    print("=" * 50)
    
    fake_ws = FakeWebSocket()
    session = DeviceSession(fake_ws, "test-device")
    sm = SessionManager()
    sm.add(session)
    
    vad = VADProcessor()
    stt = StreamingSTT()
    llm = LLMStreamer()
    tts = TTSEngine()
    router = IntentRouter()
    interrupt = InterruptManager()
    
    orch = ConversationOrchestrator(session, sm, vad, stt, llm, tts, router, interrupt)
    
    print("\n1. Starting orchestrator...")
    try:
        await orch.start()
        print("   ✓ Started")
    except Exception as e:
        print(f"   ✗ Start failed: {e}")
        traceback.print_exc()
        return
    
    print("\n2. Sending text '你好'...")
    try:
        await asyncio.wait_for(orch.handle_text_input("你好"), timeout=20)
        print("   ✓ handle_text_input completed")
    except asyncio.TimeoutError:
        print("   ✗ TIMEOUT after 20s — pipeline hung!")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        traceback.print_exc()
        await orch.stop()
        return
    
    print(f"\n3. Messages sent ({len(fake_ws.sent)}):")
    for t, d in fake_ws.sent[-10:]:
        if t == "json":
            print(f"   [{d.get('type')}] {d.get('text', d.get('state', d.get('message', '')))[:80]}")
        else:
            print(f"   [audio] {d} bytes")
    
    print("\n4. Stopping orchestrator...")
    await orch.stop()
    print("   ✓ Stopped")
    
    print(f"\n{'='*50}")
    print("TEST COMPLETE")
    print(f"{'='*50}")

asyncio.run(test())
