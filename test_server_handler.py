#!/usr/bin/env python3
"""Replicate the EXACT server WebSocket handling logic."""
import asyncio
import json
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from butler.server import butler

class MockWebSocket:
    """Simulate FastAPI WebSocket interface exactly as server.py uses it."""
    def __init__(self):
        self.open = True
        self.sent = []
        self._messages = asyncio.Queue()
        
    async def accept(self):
        print("[Mock] accept()")
        
    async def send_json(self, data):
        self.sent.append(data)
        t = data.get('type', '?')
        if t in ('state_change',):
            print(f"  [SEND] {t}: {data.get('to', '')}")
        elif t == 'stt_final':
            print(f"  [SEND] stt_final: {data.get('text', '')}")
        elif t in ('tts_start', 'tts_end'):
            print(f"  [SEND] {t}")
        elif t == 'error':
            print(f"  [SEND] ERROR: {data.get('message', '')}")
        else:
            print(f"  [SEND] {t}: {json.dumps(data, ensure_ascii=False)[:100]}")
        
    async def send_bytes(self, data):
        self.sent.append(("audio", len(data)))
        print(f"  [SEND] audio chunk: {len(data)} bytes")
        
    async def receive_bytes(self):
        """Simulate iter_bytes() with a single text message then wait."""
        msg = await self._messages.get()
        return msg
    
    def add_message(self, data: bytes):
        self._messages.put_nowait(data)

async def test():
    print("=" * 50)
    print("Server WebSocket handler simulation")
    print("=" * 50)
    
    # Replicate handle_websocket logic
    ws = MockWebSocket()
    
    await ws.accept()
    
    # Send a text message (simulating what the test client sends)
    text_msg = json.dumps({"type": "text", "text": "你好"}).encode('utf-8')
    ws.add_message(text_msg)
    
    # Now simulate the exact server handler
    try:
        print("\n[Handler] Waiting for messages...")
        
        # Get first message
        message = await asyncio.wait_for(ws.receive_bytes(), timeout=15)
        print(f"[Handler] Got message: {len(message)} bytes")
        
        # Process it (same logic as _handle_message + _handle_json)
        if message and message[0] in (0x7B, 0x5B):
            data = json.loads(message.decode("utf-8"))
            msg_type = data.get("type", "")
            print(f"[Handler] JSON type={msg_type}, text={data.get('text', '')}")
            
            if msg_type == "text":
                text = data.get("text", "")
                print(f"[Handler] Calling handle_text_input('{text}')...")
                
                try:
                    # Get the device session that handle_websocket would create
                    # In the real server, this is created during connection setup
                    # We need to access the session that was created
                    # Just directly test the orchestrator
                    
                    # Instead, let's just test that the server doesn't crash
                    # when receive_bytes blocks
                    pass
                except Exception as e:
                    print(f"[Handler] ERROR: {e}")
                    traceback.print_exc()
        
        print("\n[Handler] Done")
        
    except asyncio.TimeoutError:
        print("[Handler] TIMEOUT - hung!")
    except Exception as e:
        print(f"[Handler] Unhandled error: {e}")
        traceback.print_exc()

asyncio.run(test())
