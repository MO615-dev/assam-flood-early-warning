import http.server
import socketserver
import json
import os
import threading
import socket
import sys
import io

# Reconfigure stdout/stderr to support Unicode emoji output under Windows console
try:
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

PORT_MAIN = 8080
PORT_MOBILE = 8081
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Global list of alerts in-memory (shared between main and mobile server handlers)
alerts_history = []
alerts_lock = threading.Lock()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Handler for the Desktop Webapp (Port 8080)
class MainHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_POST(self):
        if self.path == '/api/send_alert':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            message = data.get('message', '')
            stage = data.get('stage', '')
            status = data.get('status', '')
            timestamp = data.get('timestamp', '') or "Just now"
            
            # Format time beautifully for mobile UI if timestamp is empty
            if timestamp == "Just now":
                import datetime
                timestamp = datetime.datetime.now().strftime("%I:%M %p")
            
            alert_entry = {
                "message": message,
                "stage": stage,
                "status": status,
                "timestamp": timestamp
            }
            
            # Print to local console safely
            try:
                print("\n" + "="*50)
                print("[SIMULATED FLOOD ALERT — NOT SENT]")
                print(f"  Alert Type : {status}")
                print(f"  River Stage: {stage} m")
                print(f"  Content    : \"{message}\"")
                print("="*50 + "\n")
            except Exception:
                # Fallback print without emoji formatting if console throws encoding errors
                safe_status = status.encode('ascii', errors='replace').decode('ascii') if status else ""
                safe_msg = message.encode('ascii', errors='replace').decode('ascii') if message else ""
                print("\n" + "="*50)
                print("[SIMULATED FLOOD ALERT — NOT SENT]")
                print(f"  Alert Type : {safe_status}")
                print(f"  River Stage: {stage} m")
                print(f"  Content    : \"{safe_msg}\"")
                print("="*50 + "\n")
            
            # Append to shared history under lock
            with alerts_lock:
                alerts_history.append(alert_entry)
                # Keep last 50 alerts
                if len(alerts_history) > 50:
                    alerts_history.pop(0)
            
            # Append to log file
            log_path = os.path.join(DIRECTORY, "sms_broadcasts.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[SIMULATED — NOT SENT] [{status}] Stage: {stage}m | Msg: {message}\n")
                
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "sent": True}).encode('utf-8'))
        else:
            super().do_POST()

    def do_GET(self):
        if self.path == '/api/alerts_list':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with alerts_lock:
                self.wfile.write(json.dumps(alerts_history).encode('utf-8'))
        elif self.path == '/api/ip':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"ip": get_local_ip()}).encode('utf-8'))
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-type')
        self.end_headers()

# Handler for the Mobile Alerts Screen (Port 8081)
class MobileHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        # Serve mobile.html as root directory fallback instead of listing files
        if self.path == '/' or self.path == '/index.html':
            self.path = '/mobile.html'
        
        if self.path == '/api/alerts_list':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with alerts_lock:
                self.wfile.write(json.dumps(alerts_history).encode('utf-8'))
        elif self.path == '/api/ip':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"ip": get_local_ip()}).encode('utf-8'))
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-type')
        self.end_headers()

def start_main_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT_MAIN), MainHandler) as httpd:
        print(f"[OK] Main desktop server active on: http://localhost:{PORT_MAIN}")
        httpd.serve_forever()

def start_mobile_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT_MOBILE), MobileHandler) as httpd:
        print(f"[OK] Mobile alerts server active on: http://localhost:{PORT_MOBILE}")
        httpd.serve_forever()

def run():
    ip = get_local_ip()
    print("="*60)
    print("DUAL SERVER ENGINE - ACTIVE")
    print(f"DESKTOP MONITOR: http://localhost:{PORT_MAIN}")
    print(f"MOBILE NETWORK MONITOR: http://{ip}:{PORT_MOBILE}")
    print("   (Ensure your mobile phone is on the same Wi-Fi network)")
    print("="*60)
    
    t_main = threading.Thread(target=start_main_server, daemon=True)
    t_mobile = threading.Thread(target=start_mobile_server, daemon=True)
    
    t_main.start()
    t_mobile.start()
    
    # Keep the main thread running to capture interruptions
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping servers...")

if __name__ == '__main__':
    run()
