#!/usr/bin/env python3
"""Direct test of the orchestrator pipeline (no WebSocket)."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from butler.config import config
from butler.vad import VADProcessor
from butler.stt import StreamingSTT
from butler.llm import LLMStreamer
from butler.tts import TTSEngine
from butler.router import IntentRouter, IntentType
from butler.interrupt import InterruptManager

async def test():
    print("Creating pipeline components...")
    
    vad = VADProcessor()
    stt = StreamingSTT()
    llm = LLMStreamer()
    tts = TTSEngine()
    router = IntentRouter()
    interrupt = InterruptManager()
    
    print(f"  STT: {'OK' if stt.is_available() else 'NO'}")
    print(f"  TTS: {'OK' if tts.is_available() else 'NO'}")
    
    # Route "你好"
    route = router.route("你好")
    print(f"  Route: {route.intent} (confidence={route.confidence})")
    
    # Test LLM stream directly
    print("\nTesting LLM stream...")
    system = "你是一个智能语音助手,回复简短口语化。"
    collected = []
    try:
        async for token in llm.stream_response("你好", system, []):
            collected.append(token)
            print(f"  Token: {repr(token)}")
        print(f"  Full response: {''.join(collected)}")
        print("  LLM: SUCCESS")
    except Exception as e:
        print(f"  LLM ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    # Test TTS directly
    print("\nTesting TTS synthesis...")
    try:
        audio = await tts.synthesize("你好,我是Aria")
        if audio:
            print(f"  TTS: {len(audio)} bytes of PCM audio")
        else:
            print(f"  TTS: No audio (MeloTTS not installed)")
        print("  TTS: SUCCESS")
    except Exception as e:
        print(f"  TTS ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    # Test TTS streaming
    print("\nTesting TTS streaming...")
    try:
        sentence_queue = asyncio.Queue()
        output_queue = asyncio.Queue()
        interrupt.register("test_device")
        
        tts_task = asyncio.create_task(
            tts.synthesize_stream(sentence_queue, output_queue, 
                                   interrupt._events.get("test_device", asyncio.Event()))
        )
        
        await sentence_queue.put({"text": "你好", "is_final": True})
        await sentence_queue.put({"text": None, "is_final": True})
        
        await asyncio.sleep(1)
        
        results = []
        while not output_queue.empty():
            item = output_queue.get_nowait()
            results.append(item["type"])
        
        tts_task.cancel()
        print(f"  TTS stream results: {results}")
        print("  TTS STREAM: SUCCESS")
    except Exception as e:
        print(f"  TTS STREAM ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nDone!")

asyncio.run(test())
