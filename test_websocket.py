import asyncio
import websockets
import wave
import time

async def test_audio_stream():
    # Read wav file
    wf = wave.open("test.wav", "rb")
    print(f"Sample width: {wf.getsampwidth()}, Framerate: {wf.getframerate()}, Channels: {wf.getnchannels()}")
    
    # Connect to websocket
    async with websockets.connect("ws://127.0.0.1:8000/ws/audio") as ws:
        print("Connected.")
        # Send language
        await ws.send("hi")
        
        info = await ws.recv()
        print("Received:", info)
        
        chunk_size = 4096 # samples
        bytes_per_sample = 2
        
        # We need to receive transcripts in the background
        async def recv_task():
            try:
                while True:
                    msg = await ws.recv()
                    print(f"[WS RECEIVE] {msg}")
            except Exception as e:
                pass
                
        bg_task = asyncio.create_task(recv_task())
        
        # Stream audio in real-time
        while True:
            data = wf.readframes(chunk_size)
            if not data:
                break
            
            await ws.send(data)
            # Sleep to simulate real-time
            await asyncio.sleep(chunk_size / 16000.0)
            
        print("Finished sending audio. Waiting a few seconds for transcription to finish...")
        await asyncio.sleep(5)
        bg_task.cancel()

asyncio.run(test_audio_stream())
