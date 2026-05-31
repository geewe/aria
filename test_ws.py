#!/usr/bin/env python3
"""Quick WebSocket test for Aria 家庭助手."""
import asyncio
import json
import sys

async def test():
    import websockets
    async with websockets.connect('ws://localhost:8650/ws') as ws:
        m = json.loads(await ws.recv())
        print(f'Connected: {m["device_id"]}')
        
        await ws.send(json.dumps({'type': 'text', 'text': '你好'}))
        print('Sent text, waiting for response...')
        
        try:
            while True:
                data = await asyncio.wait_for(ws.recv(), timeout=5)
                if isinstance(data, (bytes, bytearray)):
                    print(f'Audio chunk: {len(data)} bytes')
                else:
                    m = json.loads(data)
                    t = m.get('type', 'unknown')
                    if t == 'tts_end':
                        print('TTS complete')
                        break
                    print(f'{t}:', json.dumps(m, ensure_ascii=False)[:200])
        except asyncio.TimeoutError:
            print('No more messages (timeout)')
        except websockets.exceptions.ConnectionClosed:
            print('Connection closed by server')
            # Check error log
            import subprocess
            r = subprocess.run(['cat', '/tmp/butler_err.log'], capture_output=True, text=True)
            print('Server log:', r.stdout[-500:])
        
        print('SUCCESS' if ws.close_code is None else f'Closed: {ws.close_code}')

asyncio.run(test())
