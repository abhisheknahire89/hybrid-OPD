# -*- coding: utf-8 -*-
import asyncio
import websockets
import wave
import time
import os
import subprocess
import json
import requests
from concurrent.futures import ThreadPoolExecutor

# Setup file paths
WAV_PATH = "test.wav"
API_URL = "http://127.0.0.1:8000/api/generate_note"
WS_URL = "ws://127.0.0.1:8000/ws/audio"

def get_uvicorn_pids():
    try:
        output = subprocess.check_output(["lsof", "-t", "-i", ":8000"]).decode().strip()
        pids = [int(p) for p in output.split() if p.strip()]
        return list(set(pids))
    except Exception as e:
        print(f"Error finding uvicorn PIDs: {e}")
        return []

def get_total_memory_mb(pids):
    if not pids:
        return 0.0
    total_kb = 0
    for pid in pids:
        try:
            output = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
            if output:
                total_kb += int(output)
        except Exception:
            pass
    return total_kb / 1024.0

def call_generate_note_sync(transcript_text):
    t0 = time.time()
    payload = {"transcript": transcript_text}
    try:
        resp = requests.post(API_URL, json=payload, timeout=30)
        status = resp.status_code
        try:
            body = resp.json()
        except:
            body = resp.text
        latency = time.time() - t0
        return status, latency, body
    except Exception as e:
        latency = time.time() - t0
        return 500, latency, str(e)

async def test_api_quota():
    print("\n--- 1. Testing Gemini API Quota and Tier ---")
    pids = get_uvicorn_pids()
    print(f"Found Uvicorn PIDs: {pids}")
    
    transcripts = [
        f"Doctor: Patient complains of fever. Prescribed Dolo 650 twice daily for 3 days. Patient: Yes doctor."
        for _ in range(10)
    ]
    
    latencies = []
    status_codes = []
    
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as executor:
        for i, text in enumerate(transcripts):
            print(f"Making API test call {i+1}/10...")
            status, latency, body = await loop.run_in_executor(
                executor, call_generate_note_sync, text
            )
            latencies.append(latency)
            status_codes.append(status)
            print(f"Call {i+1} completed: status={status}, latency={latency:.2f}s")
            await asyncio.sleep(1.0)
            
    avg_latency = sum(latencies) / len(latencies)
    print(f"Average Call Latency: {avg_latency:.2f}s")
    print(f"Status codes returned: {status_codes}")
    
    is_free_tier = False
    if 429 in status_codes:
        print("API Key returned 429! Key appears to be on the FREE tier.")
        is_free_tier = True
    else:
        print("Sending rapid burst of 5 calls to verify tier...")
        with ThreadPoolExecutor() as executor:
            futures = [
                loop.run_in_executor(executor, call_generate_note_sync, transcripts[0])
                for _ in range(5)
            ]
            burst_results = await asyncio.gather(*futures)
            burst_status = [r[0] for r in burst_results]
            print(f"Burst status codes: {burst_status}")
            if 429 in burst_status:
                print("Rapid burst returned 429. Key is on the FREE tier.")
                is_free_tier = True
            else:
                print("No 429 returned in burst. Key might be on the PAID tier or has higher concurrent limits.")
                
    return is_free_tier, avg_latency

async def stream_audio_websocket(session_id):
    wf = wave.open(WAV_PATH, "rb")
    chunk_size = 4096  # samples
    
    # Stagger connection starts slightly to avoid collision
    await asyncio.sleep(session_id * 0.2)
    
    t_start = time.time()
    try:
        async with websockets.connect(WS_URL) as ws:
            await ws.send("hi")
            await ws.recv() # read info
            
            received_texts = []
            async def recv_task():
                try:
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if data.get("type") == "transcript":
                            received_texts.append(data.get("text"))
                except Exception:
                    pass
            bg = asyncio.create_task(recv_task())
            
            while True:
                data = wf.readframes(chunk_size)
                if not data:
                    break
                await ws.send(data)
                await asyncio.sleep(chunk_size / 16000.0)
                
            await asyncio.sleep(2.0)
            bg.cancel()
            return len(received_texts), time.time() - t_start, received_texts
    except Exception as e:
        print(f"Session {session_id} failed: {e}")
        return 0, time.time() - t_start, []

async def test_concurrency(concurrent_users):
    print(f"\n--- Testing WS Concurrency with {concurrent_users} users ---")
    pids = get_uvicorn_pids()
    mem_start = get_total_memory_mb(pids)
    print(f"Memory before concurrent test: {mem_start:.2f} MB")
    
    tasks = [stream_audio_websocket(i) for i in range(concurrent_users)]
    
    t0 = time.time()
    results = await asyncio.gather(*tasks)
    duration = time.time() - t0
    
    mem_end = get_total_memory_mb(pids)
    print(f"Memory after concurrent test: {mem_end:.2f} MB")
    print(f"Memory increase: {mem_end - mem_start:.2f} MB")
    
    success_sessions = 0
    for idx, (count, dur, texts) in enumerate(results):
        print(f"User {idx+1} received {count} transcript segments. Duration: {dur:.2f}s")
        if count > 0:
            success_sessions += 1
            
    print(f"Successful sessions: {success_sessions}/{concurrent_users}")
    return mem_end - mem_start, success_sessions == concurrent_users

async def test_simultaneous_note_generation(concurrent_requests):
    print(f"\n--- Testing API generate_note latency with {concurrent_requests} simultaneous requests ---")
    transcript = "Doctor: Patient presents with fever and cough for 2 days. Prescribed Dolo 650 twice daily. Patient: Yes doctor."
    
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as executor:
        t0 = time.time()
        futures = [
            loop.run_in_executor(executor, call_generate_note_sync, transcript)
            for _ in range(concurrent_requests)
        ]
        results = await asyncio.gather(*futures)
        duration = time.time() - t0
        
        print(f"Completed {concurrent_requests} calls in {duration:.2f} seconds.")
        for idx, (status, latency, body) in enumerate(results):
            print(f"Request {idx+1}: status={status}, latency={latency:.2f}s")

async def main():
    pids = get_uvicorn_pids()
    if not pids:
        print("Uvicorn is not running on port 8000. Please start the server first.")
        return
        
    mem_idle = get_total_memory_mb(pids)
    print(f"Uvicorn Process RSS at Idle: {mem_idle:.2f} MB")
    
    # 1. API Quota & Tier Test
    is_free_tier, avg_latency = await test_api_quota()
    
    # 2. Concurrency WS Session tests
    print("\n--- 2. Testing baseline single WS user ---")
    t_single = asyncio.create_task(stream_audio_websocket(0))
    
    await asyncio.sleep(2.0)
    mem_active_1 = get_total_memory_mb(pids)
    print(f"Memory with 1 active user: {mem_active_1:.2f} MB")
    
    res_single = await t_single
    print(f"Baseline single user completed. Received {res_single[0]} transcripts.")
    
    mem_inc_2, ok_2 = await test_concurrency(2)
    mem_inc_3, ok_3 = await test_concurrency(3)
    mem_inc_5, ok_5 = await test_concurrency(5)
    
    # 3. API Concurrency Test
    await test_simultaneous_note_generation(1)
    await test_simultaneous_note_generation(3)
    await test_simultaneous_note_generation(5)
    
    print("\n================ DIAGNOSTIC SUMMARY ================")
    print(f"Tier: {'FREE' if is_free_tier else 'PAID'}")
    print(f"Uvicorn Idle RSS: {mem_idle:.2f} MB")
    print(f"Uvicorn RAM with 1 user: {mem_active_1:.2f} MB")
    print(f"Uvicorn RAM delta for 1 user: {mem_active_1 - mem_idle:.2f} MB")
    print(f"2 concurrent users test: {'PASSED' if ok_2 else 'FAILED (audio loss or errors)'}")
    print(f"3 concurrent users test: {'PASSED' if ok_3 else 'FAILED (audio loss or errors)'}")
    print(f"5 concurrent users test: {'PASSED' if ok_5 else 'FAILED (audio loss or errors)'}")
    
if __name__ == "__main__":
    asyncio.run(main())
