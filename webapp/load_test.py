import urllib.request
import json
import threading
import time
import random

API_URL_POST = "http://localhost:8080/api/send_alert"
API_URL_GET = "http://localhost:8081/api/alerts_list"

success_count = 0
error_count = 0
stats_lock = threading.Lock()

def make_post_request(idx):
    global success_count, error_count
    data = {
        "message": f"LOAD TEST: Simulated flood warning alert step #{idx}",
        "stage": round(random.uniform(5.0, 11.5), 2),
        "status": random.choice(["WARNING Stage", "CRITICAL Warning", "ALL-CLEAR Safe"]),
        "timestamp": time.strftime("%I:%M %p")
    }
    req = urllib.request.Request(
        API_URL_POST,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=1.0) as response:
            if response.status == 200:
                with stats_lock:
                    success_count += 1
            else:
                with stats_lock:
                    error_count += 1
    except Exception as e:
        with stats_lock:
            error_count += 1

def make_get_request():
    global success_count, error_count
    try:
        with urllib.request.urlopen(API_URL_GET, timeout=1.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                with stats_lock:
                    success_count += 1
            else:
                with stats_lock:
                    error_count += 1
    except Exception as e:
        with stats_lock:
            error_count += 1

def worker_thread(thread_id):
    for i in range(25):
        # 40% write, 60% read
        if random.random() < 0.4:
            make_post_request(f"T{thread_id}-{i}")
        else:
            make_get_request()
        time.sleep(0.01)

def run_load_test():
    print("Starting load test on server API endpoints (10 concurrent threads, 25 requests each)...")
    start_time = time.time()
    
    threads = []
    for i in range(10):
        t = threading.Thread(target=worker_thread, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    duration = time.time() - start_time
    print("\n" + "="*50)
    print("LOAD TEST RESULTS")
    print(f"  Duration    : {duration:.2f} seconds")
    print(f"  Total Req   : {success_count + error_count}")
    print(f"  Success Req : {success_count}")
    print(f"  Failed Req  : {error_count}")
    print(f"  Throughput  : {(success_count + error_count)/duration:.2f} req/sec")
    print("="*50 + "\n")

if __name__ == '__main__':
    run_load_test()
