import cv2
import base64
import socketio
import threading
import time
import queue

class OptimizedFaceStreamClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.sio = socketio.Client()
        self.cap = cv2.VideoCapture(0)
        self.streaming = False
        self.frame_queue = queue.Queue(maxsize=2)
        
        # Kamera optimiert für niedrige Latenz
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 480)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.setup_events()
    
    def setup_events(self):
        @self.sio.event
        def connect():
            print("Verbindung zur VM hergestellt")
            self.streaming = True
            # Capture und Send in separaten Threads
            threading.Thread(target=self.capture_frames, daemon=True).start()
            threading.Thread(target=self.send_frames, daemon=True).start()
        
        @self.sio.event
        def disconnect():
            print("Verbindung getrennt")
            self.streaming = False
        
        @self.sio.event
        def connect_error(data):
            print(f"Verbindungsfehler: {data}")
        
        @self.sio.event
        def recognition_result(data):
            if data.get('face_recognized'):
                name = data.get('user_name', 'Unbekannt')
                confidence = data.get('confidence', 0)
                print(f"{name} ({confidence:.1%})")
    
    def capture_frames(self):
        """Kontinuierliches Frame-Capturing"""
        print("Frame-Capture gestartet")
        while self.streaming:
            ret, frame = self.cap.read()
            if ret:
                # Queue leeren wenn voll (nur neueste Frames)
                while not self.frame_queue.empty():
                    try:
                        self.frame_queue.get_nowait()
                    except queue.Empty:
                        break
                
                try:
                    self.frame_queue.put_nowait(frame)
                except queue.Full:
                    pass
            
            time.sleep(0.033)  # ~30 FPS Capture
    
    def send_frames(self):
        """Frame-Versendung mit Kompression"""
        print("Frame-Versendung gestartet")
        frame_skip = 0
        
        while self.streaming:
            try:
                frame = self.frame_queue.get(timeout=0.1)
                
                # Nur jeden 2. Frame senden
                frame_skip += 1
                if frame_skip % 2 != 0:
                    continue
                
                # Kompression
                encode_params = [
                    cv2.IMWRITE_JPEG_QUALITY, 50,
                    cv2.IMWRITE_JPEG_OPTIMIZE, 1
                ]
                
                ret, buffer = cv2.imencode('.jpg', frame, encode_params)
                if ret:
                    frame_b64 = base64.b64encode(buffer).decode('utf-8')
                    self.sio.emit('video_frame', {'image': frame_b64})
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Send-Fehler: {e}")
                if not self.streaming:
                    break
    
    def connect_to_server(self):
        try:
            print("Verbinde mit Server...")
            # Timeout-Parameter entfernt für Kompatibilität
            self.sio.connect(self.server_url)
            
            # Warte auf Verbindung oder Fehler
            print("Warte auf Verbindung...")
            self.sio.wait()
            
        except socketio.exceptions.ConnectionError as e:
            print(f"Socket.IO Verbindungsfehler: {e}")
        except Exception as e:
            print(f"Allgemeiner Verbindungsfehler: {e}")
    
    def cleanup(self):
        print("Aufräumen...")
        self.streaming = False
        time.sleep(0.5)
        
        if self.cap and self.cap.isOpened():
            self.cap.release()
            print("Kamera freigegeben")
            
        if self.sio.connected:
            self.sio.disconnect()
            print("Socket getrennt")

if __name__ == "__main__":
    SERVER_URL = "http://141.72.12.186:5000"
    
    print("Starte Video-Stream... (Strg+C zum Beenden)")
    print(f"Server: {SERVER_URL}")
    
    # Überprüfe Kamera
    test_cap = cv2.VideoCapture(0)
    if not test_cap.isOpened():
        print("Keine Kamera gefunden!")
        exit(1)
    test_cap.release()
    print("Kamera OK")
    
    client = OptimizedFaceStreamClient(SERVER_URL)
    try:
        client.connect_to_server()
    except KeyboardInterrupt:
        print("\nBenutzer-Unterbrechung")
    except Exception as e:
        print(f"\nUnerwarteter Fehler: {e}")
    finally:
        client.cleanup()
        print("Programm beendet")
