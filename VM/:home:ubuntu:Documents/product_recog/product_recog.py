#!/usr/bin/env python3
"""
Product Recognition Stream Server
Echtzeit-Produkterkennung über HTTP Video Stream mit Web Interface

Basiert auf VideoProductRecognizer, erweitert um Flask/SocketIO Web-Interface
ähnlich wie das Face Recognition System

NEUE FEATURES:
- Preissystem für Models (0->0,99€, 1->1,49€, 2->2,49€, 3->0,49€)
- Automatisches Schreiben erkannter Produkte in txt-Datei
- Gesamtpreis-Berechnung
"""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import cv2
import numpy as np
import math
import os
import time
import base64
import threading
import queue
import json
from datetime import datetime
from collections import deque
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'product_recognition_secret'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

class ProductStreamRecognizer:
    def __init__(self, models_path="./models/"):
        """
        Initialisiert den Product Stream Recognizer für Web-Interface
        """
        self.models_path = models_path
        self.models = {}
        self.sift = None
        self.is_running = False
        
        # PREISSYSTEM - Feste Preise für Models
        self.model_prices = {
            0: 0.99,
            1: 1.49,
            2: 2.49,
            3: 0.49,
            4: 1.99,
            5: 3.49,
            6: 0.79,
            7: 2.99,
            8: 1.29,
            9: 4.99
        }
        
        # Ausgabedateien
        self.output_file = "./detected_products.txt"
        self.session_file = "./current_session.json"
        
        # Performance-Parameter für Stream
        self.FRAME_SKIP = 2
        self.RESIZE_FACTOR = 0.6
        
        # STRENGERE Erkennungsparameter
        self.MIN_MATCHES = 8           # War: 8 → Jetzt: 15 (mehr Matches erforderlich)
        self.MATCHING_THRESHOLD = 0.4   # War: 0.6 → Jetzt: 0.4 (strengeres Matching)
        self.COLOR_DIFF_THRESHOLD = 70  # War: 100 → Jetzt: 70 (strengere Farbprüfung)
        
        # NEUE Parameter für zusätzliche Validierung
        self.MIN_CONFIDENCE = 0.6       # Minimale Konfidenz für Anzeige
        self.MIN_AREA = 3000            # Minimale Bounding Box Fläche (Pixel)
        self.MAX_AREA_RATIO = 0.8       # Max. 80% des Bildes
        self.GEOMETRIC_VALIDATION = True # Geometrische Validierung aktivieren
 
        # Threading für Stream-Verarbeitung
        self.frame_queue = queue.Queue(maxsize=3)
        self.result_queue = queue.Queue(maxsize=3)
        self.processing_active = True
        
        # Letzte Erkennung für Duplikat-Vermeidung
        self.last_detection = {}
        self.detection_cooldown = 2.0  # Sekunden zwischen gleichen Erkennungen
        
        # Farben für verschiedene Produkte
        self.colors = [
            (0, 255, 0),    # Grün
            (255, 0, 0),    # Rot  
            (0, 0, 255),    # Blau
            (255, 255, 0),  # Gelb
            (255, 0, 255),  # Magenta
            (0, 255, 255),  # Cyan
            (128, 0, 128),  # Lila
            (255, 165, 0),  # Orange
            (128, 128, 0),  # Olive
            (0, 128, 128),  # Teal
            (128, 0, 0),    # Maroon
        ]
        
        # Video-Stream Setup
        self.video_capture = None
        self.stream_url = None
        
        self.init_sift()
        self.load_models()
        self.init_output_files()

    def init_output_files(self):
        """Initialisiert die Ausgabedateien"""
        try:
            # Erstelle Header für detected_products.txt
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write("=== ERKANNTE PRODUKTE MIT PREISEN ===\n")
                f.write("Format: Zeitstempel | Produktname | Preis(€) | Konfidenz(%) | Model-ID\n")
                f.write("=" * 80 + "\n")
            
            # Erstelle session file
            session_data = {
                "session_start": datetime.now().isoformat(),
                "total_products": 0,
                "total_value": 0.0,
                "products": []
            }
            
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            
            print(f"✓ Output files initialized:")
            print(f"  - Products: {self.output_file}")
            print(f"  - Session:  {self.session_file}")
            
        except Exception as e:
            print(f"✗ Error initializing output files: {e}")

    def write_detected_products(self, products):
        """
        Schreibt erkannte Produkte in txt-Datei und aktualisiert Session
        """
        if not products:
            return
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_time = time.time()
        
        # Filtere neue Erkennungen (Duplikat-Vermeidung)
        new_detections = []
        for product in products:
            product_key = f"{product['id']}_{product['name']}"
            
            # Prüfe ob das Produkt kürzlich erkannt wurde
            if (product_key not in self.last_detection or 
                current_time - self.last_detection[product_key] > self.detection_cooldown):
                
                self.last_detection[product_key] = current_time
                new_detections.append(product)
        
        if not new_detections:
            return
        
        try:
            # Schreibe in txt-Datei
            with open(self.output_file, 'a', encoding='utf-8') as f:
                for product in new_detections:
                    model_id = product['id']
                    name = product['name']
                    confidence = product['confidence'] * 100
                    price = self.model_prices.get(model_id, 0.0)
                    
                    # Format: Zeitstempel | Produktname | Preis(€) | Konfidenz(%) | Model-ID
                    line = f"{timestamp} | {name} | {price:.2f}€ | {confidence:.1f}% | Model-{model_id}\n"
                    f.write(line)
            
            # Aktualisiere Session-Datei
            total_value = sum(self.model_prices.get(p['id'], 0.0) for p in new_detections)
            self.update_session_file(new_detections, total_value)
            
            print(f"💾 PRODUKTE GESPEICHERT: {len(new_detections)} neue Erkennungen - Wert: {total_value:.2f}€")
            
        except Exception as e:
            print(f"✗ Error writing products: {e}")

    def update_session_file(self, new_products, session_value):
        """Aktualisiert die Session-Datei mit neuen Produkten"""
        try:
            # Lade aktuelle Session
            with open(self.session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            
            # Aktualisiere Statistiken
            session_data["total_products"] += len(new_products)
            session_data["total_value"] += session_value
            session_data["last_update"] = datetime.now().isoformat()
            
            # Füge neue Produkte hinzu
            for product in new_products:
                product_data = {
                    "timestamp": datetime.now().isoformat(),
                    "name": product['name'],
                    "model_id": product['id'],
                    "price_euro": self.model_prices.get(product['id'], 0.0),
                    "confidence": product['confidence']
                }
                session_data["products"].append(product_data)
            
            # Speichere aktualisierte Session
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"✗ Error updating session: {e}")

    def get_session_summary(self):
        """Gibt Session-Zusammenfassung zurück"""
        try:
            with open(self.session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            return session_data
        except:
            return {
                "total_products": 0,
                "total_value": 0.0,
                "products": []
            }

    def init_sift(self):
        """Initialisiert SIFT Detektor"""
        try:
            self.sift = cv2.SIFT_create()
            print("✓ SIFT initialisiert")
        except AttributeError:
            try:
                self.sift = cv2.xfeatures2d.SIFT_create()
                print("✓ SIFT (xfeatures2d) initialisiert")
            except AttributeError:
                raise Exception("SIFT nicht verfügbar. Installiere opencv-contrib-python")

    def load_models(self, max_models=10):
        """Lädt alle verfügbaren Produktmodelle mit Preisen"""
        print(f"\n=== LADE {max_models} PRODUCT MODELS MIT PREISEN ===")
        
        # Erstelle models Ordner falls nicht vorhanden
        os.makedirs(self.models_path, exist_ok=True)
        
        loaded_count = 0
        for i in range(max_models):
            model_paths = [
                os.path.join(self.models_path, f"{i}.jpg"),
                os.path.join(self.models_path, f"{i}.png"),
                os.path.join(self.models_path, f"product_{i}.jpg"),
                os.path.join(self.models_path, f"product_{i}.png"),
            ]
            
            model_img = None
            used_path = None
            for path in model_paths:
                if os.path.exists(path):
                    model_img = cv2.imread(path, cv2.IMREAD_COLOR)
                    if model_img is not None:
                        used_path = path
                        break
            
            if model_img is not None:
                # SIFT Features berechnen
                kp_model, des_model = self.sift.detectAndCompute(model_img, None)
                
                if des_model is not None and len(kp_model) > 0:
                    # Produktname aus Dateiname extrahieren
                    filename = os.path.basename(used_path)
                    name = os.path.splitext(filename)[0]
                    if name.isdigit():
                        name = f"Product_{name}"
                    
                    # Preis aus Preissystem holen
                    price = self.model_prices.get(i, 0.0)
                    
                    self.models[i] = {
                        'image': model_img,
                        'keypoints': kp_model,
                        'descriptors': des_model,
                        'num_features': len(kp_model),
                        'name': name,
                        'path': used_path,
                        'price_euro': price,
                        'has_price': True if price > 0 else False
                    }
                    print(f"✓ Model {i}: {name} - {len(kp_model)} keypoints - {price:.2f}€")
                    loaded_count += 1
                else:
                    print(f"✗ Model {i}: Keine Features in {used_path}")
        
        print(f"✓ {loaded_count} Product Models mit Preisen geladen")
        return loaded_count > 0

    def connect_to_stream(self, stream_url):
        """Verbindet sich mit Raspberry Pi Video-Stream"""
        print(f"🔗 Verbinde mit Stream: {stream_url}")
        
        try:
            # Teste verschiedene Stream-URLs
            possible_urls = [
                stream_url,
                f"{stream_url}/video",
                f"{stream_url}:8000/stream.mjpg",
                f"{stream_url}:8080/stream.mjpg",
            ]
            
            for url in possible_urls:
                print(f"   Teste: {url}")
                cap = cv2.VideoCapture(url)
                
                if cap.isOpened():
                    # Teste ob Frame gelesen werden kann
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        print(f"✓ Stream erfolgreich: {url}")
                        self.video_capture = cap
                        self.stream_url = url
                        return True
                    else:
                        print(f"   Frame-Test fehlgeschlagen")
                        cap.release()
                else:
                    print(f"   Kann nicht öffnen")
                    cap.release()
            
            print("✗ Alle Stream-URLs fehlgeschlagen")
            return False
            
        except Exception as e:
            print(f"✗ Stream-Verbindung fehlgeschlagen: {e}")
            return False

    def disconnect_stream(self):
        """Trennt Stream-Verbindung"""
        if self.video_capture:
            self.video_capture.release()
            self.video_capture = None
            print("✓ Stream getrennt")

    def distance_2_points(self, A, B):
        """Berechnet Euclidische Distanz zwischen zwei Punkten"""
        return math.sqrt(np.power(A[0] - B[0], 2) + np.power(A[1] - B[1], 2))

    def match_features(self, model_descriptors, scene_descriptors):
        """Findet Matches zwischen Model und Scene Features"""
        if model_descriptors is None or scene_descriptors is None:
            return []
            
        bf = cv2.BFMatcher()
        
        try:
            matches = bf.knnMatch(model_descriptors, scene_descriptors, k=2)
        except cv2.error:
            return []
        
        good = []
        for match_pair in matches:
            if len(match_pair) == 2:
                m, n = match_pair
                if m.distance < self.MATCHING_THRESHOLD * n.distance:
                    good.append(m)
                    
        return good

    def validate_bounding_box(self, corners, frame_shape):
        """Validiert und korrigiert Bounding Box"""
        if corners is None or len(corners) != 4:
            return None
            
        # Finde min/max Koordinaten
        x_coords = [corner[0][0] for corner in corners]
        y_coords = [corner[0][1] for corner in corners]
        
        x_min, x_max = int(min(x_coords)), int(max(x_coords))
        y_min, y_max = int(min(y_coords)), int(max(y_coords))
        
        # Prüfe Bildgrenzen
        height, width = frame_shape[:2]
        x_min = max(0, min(x_min, width))
        y_min = max(0, min(y_min, height))
        x_max = max(0, min(x_max, width))
        y_max = max(0, min(y_max, height))
        
        # Prüfe minimale Größe
        if (x_max - x_min) < 40 or (y_max - y_min) < 40:
            return None
            
        return [(x_min, y_min), (x_max, y_max)]

    def quick_color_check(self, model_img, scene_crop):
        """Schnelle Farbvalidierung"""
        if scene_crop.size == 0:
            return False
            
        # Berechne Durchschnittsfarben
        model_mean = cv2.mean(model_img)[:3]
        scene_mean = cv2.mean(scene_crop)[:3]
        
        # Vergleiche Farbdifferenz
        color_diff = sum(abs(model_mean[i] - scene_mean[i]) for i in range(3)) / 3
        
        return color_diff < self.COLOR_DIFF_THRESHOLD

    def recognize_products_in_frame(self, frame):
        """
        Erkennt Produkte in einem Frame für Web-Interface mit Preisen
        """
        results = []
        
        if frame is None or frame.size == 0:
            return {
                'products_found': False,
                'product_count': 0,
                'products': [],
                'total_value': 0.0,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'frame_processed': True
            }
        
        # Frame für SIFT verkleinern
        small_frame = cv2.resize(frame, (0, 0), fx=self.RESIZE_FACTOR, fy=self.RESIZE_FACTOR)
        
        # SIFT Features im Frame berechnen
        kp_scene, des_scene = self.sift.detectAndCompute(small_frame, None)
        
        if des_scene is None or len(kp_scene) == 0:
            return {
                'products_found': False,
                'product_count': 0,
                'products': [],
                'total_value': 0.0,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'message': 'Keine Features im Frame gefunden'
            }
        
        detected_products = []
        
        # Teste jedes Model
        for model_id, model_data in self.models.items():
            # Feature Matching
            good_matches = self.match_features(model_data['descriptors'], des_scene)
            
            if len(good_matches) < self.MIN_MATCHES:
                continue
            
            # Homographie berechnen
            src_pts = np.float32([model_data['keypoints'][m.queryIdx].pt for m in good_matches]).reshape(-1,1,2)
            dst_pts = np.float32([kp_scene[m.trainIdx].pt for m in good_matches]).reshape(-1,1,2)
            
            # Skaliere Punkte zurück auf Originalgröße
            dst_pts = dst_pts / self.RESIZE_FACTOR
            
            try:
                M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                if M is None:
                    continue
            except:
                continue
            
            # Model-Ecken transformieren
            h, w = model_data['image'].shape[:2]
            corners = np.float32([[0,0],[0,h-1],[w-1,h-1],[w-1,0]]).reshape(-1,1,2)
            
            try:
                transformed_corners = cv2.perspectiveTransform(corners, M)
            except:
                continue
            
            # Bounding Box validieren
            bbox = self.validate_bounding_box(transformed_corners, frame.shape)
            if bbox is None:
                continue
            
            # Schnelle Farbprüfung
            top_left, bottom_right = bbox
            scene_crop = frame[top_left[1]:bottom_right[1], top_left[0]:bottom_right[0]]
            
            if not self.quick_color_check(model_data['image'], scene_crop):
                continue
            
            # Konfidenz basierend auf Matches berechnen
            confidence = min(len(good_matches) / 20.0, 1.0)  # Normalisiert auf 0-1
            
            # Produkt-Info zusammenstellen MIT PREIS
            product_info = {
                'id': model_id,
                'name': model_data['name'],
                'confidence': confidence,
                'matches': len(good_matches),
                'price_euro': model_data['price_euro'],
                'has_price': model_data['has_price'],
                'bbox': {
                    'left': bbox[0][0],
                    'top': bbox[0][1],
                    'right': bbox[1][0],
                    'bottom': bbox[1][1]
                },
                'color_id': model_id % len(self.colors)
            }
            
            detected_products.append(product_info)
        
        # Sortiere nach Konfidenz
        detected_products.sort(key=lambda x: x['confidence'], reverse=True)
        
        # Berechne Gesamtwert
        total_value = sum(p['price_euro'] for p in detected_products if p['has_price'])
        
        # SCHREIBE ERKANNTE PRODUKTE IN DATEI
        if detected_products:
            self.write_detected_products(detected_products)
        
        return {
            'products_found': len(detected_products) > 0,
            'product_count': len(detected_products),
            'products': detected_products,
            'total_value': total_value,
            'timestamp': datetime.now().strftime("%H:%M:%S"),
            'frame_processed': True,
            'best_product': detected_products[0]['name'] if detected_products else 'Suche...',
            'best_confidence': detected_products[0]['confidence'] if detected_products else 0
        }

    def process_frame_from_base64(self, image_b64):
        """Verarbeitet Frame aus Base64-String"""
        try:
            # Base64 zu Image
            image_bytes = base64.b64decode(image_b64)
            nparr = np.frombuffer(image_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            return self.recognize_products_in_frame(frame)
            
        except Exception as e:
            print(f"Frame processing error: {e}")
            return {
                'products_found': False,
                'product_count': 0,
                'products': [],
                'total_value': 0.0,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'error': str(e)
            }

# Globale Variablen
recognizer = ProductStreamRecognizer()
current_recognition = {
    'products_found': False, 
    'product_count': 0, 
    'products': [], 
    'total_value': 0.0,
    'best_product': 'Warte...', 
    'best_confidence': 0
}
processing_queue = deque(maxlen=3)
processing_active = True
frame_count = 0

def background_processor():
    """Hintergrund-Thread für Produkterkennung"""
    global current_recognition, processing_active
    
    while processing_active:
        try:
            if processing_queue:
                image_b64 = processing_queue.popleft()
                result = recognizer.process_frame_from_base64(image_b64)
                current_recognition = result
                
                # Ergebnis an alle Clients senden
                socketio.emit('recognition_result', result)
                
                if result['products_found']:
                    print(f"🎯 PRODUKTE ERKANNT: {result['product_count']} - Bestes: {result['best_product']} ({result['best_confidence']:.1%}) - Wert: {result['total_value']:.2f}€")
            else:
                time.sleep(0.05)
        except Exception as e:
            print(f"Processing error: {e}")
            time.sleep(0.1)

# REST API Endpoints
@app.route('/health')
def health_check():
    """Server-Status"""
    session_summary = recognizer.get_session_summary()
    
    return jsonify({
        'status': 'running',
        'models_loaded': len(recognizer.models),
        'models': [{'name': model['name'], 'price': model['price_euro']} for model in recognizer.models.values()],
        'stream_connected': recognizer.video_capture is not None,
        'stream_url': recognizer.stream_url,
        'session_products': session_summary.get('total_products', 0),
        'session_value': session_summary.get('total_value', 0.0),
        'output_file': recognizer.output_file,
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route('/models')
def list_models():
    """Alle geladenen Models mit Preisen auflisten"""
    models_info = []
    for model_id, model_data in recognizer.models.items():
        models_info.append({
            'id': model_id,
            'name': model_data['name'],
            'features': model_data['num_features'],
            'price_euro': model_data['price_euro'],
            'has_price': model_data['has_price'],
            'path': model_data['path']
        })
    
    return jsonify({
        'models': models_info,
        'count': len(models_info),
        'total_model_value': sum(m['price_euro'] for m in models_info)
    })

@app.route('/session_summary')
def get_session_summary():
    """Session-Zusammenfassung"""
    return jsonify(recognizer.get_session_summary())

@app.route('/detected_products')
def get_detected_products():
    """Liste der erkannten Produkte aus Datei"""
    try:
        products = []
        if os.path.exists(recognizer.output_file):
            with open(recognizer.output_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            # Parsiere nur Produkt-Zeilen (nicht Header)
            for line in lines:
                if '|' in line and not line.startswith('='):
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 5:
                        products.append({
                            'timestamp': parts[0],
                            'name': parts[1],
                            'price': parts[2],
                            'confidence': parts[3],
                            'model_id': parts[4]
                        })
        
        return jsonify({
            'products': products[-50:],  # Letzte 50 Erkennungen
            'total_count': len(products),
            'file_path': recognizer.output_file
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reset_session', methods=['POST'])
def reset_session():
    """Session zurücksetzen"""
    try:
        recognizer.init_output_files()
        recognizer.last_detection = {}
        
        global current_recognition
        current_recognition = {
            'products_found': False, 
            'product_count': 0, 
            'products': [], 
            'total_value': 0.0,
            'best_product': 'Session zurückgesetzt', 
            'best_confidence': 0
        }
        
        return jsonify({
            'success': True,
            'message': 'Session zurückgesetzt',
            'output_file': recognizer.output_file
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/add_model', methods=['POST'])
def add_model():
    """Neues Produktmodell hinzufügen"""
    try:
        if 'image' not in request.files:
            return jsonify({'error': 'No image provided'}), 400
        
        if 'name' not in request.form:
            return jsonify({'error': 'No name provided'}), 400
        
        image_file = request.files['image']
        name = request.form['name'].strip()
        
        if image_file.filename == '':
            return jsonify({'error': 'No image selected'}), 400
        
        if not name:
            return jsonify({'error': 'Name cannot be empty'}), 400
        
        # Finde nächste verfügbare ID
        next_id = max(recognizer.models.keys()) + 1 if recognizer.models else 0
        
        filename = secure_filename(f"{name}.jpg")
        filepath = os.path.join(recognizer.models_path, filename)
        
        image_file.save(filepath)
        
        try:
            # Lade Model neu
            recognizer.load_models()
            
            price = recognizer.model_prices.get(next_id, 0.0)
            
            return jsonify({
                'success': True,
                'message': f'Model {name} hinzugefügt',
                'name': name,
                'price': price,
                'total_models': len(recognizer.models)
            })
            
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': f'Failed to process model: {str(e)}'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/connect_stream', methods=['POST'])
def connect_stream():
    """Verbinde mit Raspberry Pi Stream"""
    try:
        data = request.get_json()
        stream_url = data.get('url', '')
        
        if not stream_url:
            return jsonify({'error': 'Stream URL required'}), 400
        
        success = recognizer.connect_to_stream(stream_url)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Stream verbunden: {recognizer.stream_url}',
                'url': recognizer.stream_url
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Stream-Verbindung fehlgeschlagen'
            }), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/disconnect_stream', methods=['POST'])
def disconnect_stream():
    """Trenne Stream-Verbindung"""
    recognizer.disconnect_stream()
    return jsonify({
        'success': True,
        'message': 'Stream getrennt'
    })

@app.route('/stream_frame')
def get_stream_frame():
    """Hole aktuellen Frame vom Stream"""
    if not recognizer.video_capture:
        return jsonify({'error': 'No stream connected'}), 400
    
    ret, frame = recognizer.video_capture.read()
    if not ret:
        return jsonify({'error': 'Failed to read frame'}), 500
    
    # Frame zu Base64 konvertieren
    _, buffer = cv2.imencode('.jpg', frame)
    frame_b64 = base64.b64encode(buffer).decode('utf-8')
    
    return jsonify({
        'success': True,
        'image': frame_b64,
        'timestamp': datetime.now().isoformat()
    })

# Web Interface
@app.route('/')
def index():
    return render_template('product_recognition.html')

@app.route('/metrics')
def get_metrics():
    """Live-Metriken"""
    global current_recognition, frame_count
    session_summary = recognizer.get_session_summary()
    
    return jsonify({
        'products_found': current_recognition.get('products_found', False),
        'product_count': current_recognition.get('product_count', 0),
        'total_value': current_recognition.get('total_value', 0.0),
        'best_product': current_recognition.get('best_product', 'None'),
        'best_confidence': current_recognition.get('best_confidence', 0) * 100,
        'session_products': session_summary.get('total_products', 0),
        'session_value': session_summary.get('total_value', 0.0),
        'queue_size': len(processing_queue),
        'frames_processed': frame_count,
        'models_loaded': len(recognizer.models),
        'output_file': recognizer.output_file,
        'timestamp': datetime.now().isoformat()
    })

# Socket.IO Events
@socketio.on('connect')
def handle_connect():
    print("Client verbunden")
    emit('recognition_result', current_recognition)

@socketio.on('disconnect')
def handle_disconnect():
    print("Client getrennt")

@socketio.on('video_frame')
def handle_video_frame(data):
    """Empfängt Video-Frames über Socket.IO"""
    global frame_count
    
    if 'image' in data:
        if len(processing_queue) < processing_queue.maxlen:
            processing_queue.append(data['image'])
            frame_count += 1
        
        # Frame an alle Clients weiterleiten
        emit('video_frame', {'image': data['image']}, broadcast=True)

@socketio.on('stream_frame_request')
def handle_stream_frame_request():
    """Client fordert Frame vom Stream an"""
    if recognizer.video_capture:
        ret, frame = recognizer.video_capture.read()
        if ret:
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            frame_b64 = base64.b64encode(buffer).decode('utf-8')
            emit('stream_frame', {'image': frame_b64})

if __name__ == '__main__':
    print("Starte Product Recognition Stream Server mit Preissystem...")
    
    # Models laden
    if not recognizer.load_models():
        print("⚠️  Keine Models gefunden. Verwende /add_model um Produkte hinzuzufügen.")
    else:
        print(f"\n💰 PREISSYSTEM AKTIV:")
        for model_id, price in recognizer.model_prices.items():
            if model_id in recognizer.models:
                print(f"   Model {model_id}: {recognizer.models[model_id]['name']} = {price:.2f}€")
    
    # Background-Processor starten
    processor_thread = threading.Thread(target=background_processor, daemon=True)
    processor_thread.start()
    
    print(f"\n{'='*60}")
    print("PRODUCT RECOGNITION STREAM SERVER MIT PREISSYSTEM")
    print(f"{'='*60}")
    print(f"Models geladen: {len(recognizer.models)}")
    print(f"Output-Datei: {recognizer.output_file}")
    print(f"Session-Datei: {recognizer.session_file}")
    print("Server läuft auf http://0.0.0.0:5000")
    print("\nAPI Endpoints:")
    print("  GET  /health                     - Server-Status mit Preisen")
    print("  GET  /models                     - Alle Models mit Preisen")
    print("  GET  /session_summary            - Session-Zusammenfassung")
    print("  GET  /detected_products          - Erkannte Produkte aus Datei")
    print("  POST /reset_session              - Session zurücksetzen")
    print("  POST /add_model                  - Neues Model hinzufügen")
    print("  POST /connect_stream             - Stream verbinden")
    print("  POST /disconnect_stream          - Stream trennen")
    print("  GET  /stream_frame               - Aktueller Frame")
    print("  GET  /metrics                    - Live-Metriken mit Preisen")
    print("\nWeb Interface: http://localhost:5000")
    print(f"\n📄 AUSGABE-DATEIEN:")
    print(f"   txt: {recognizer.output_file}")
    print(f"   json: {recognizer.session_file}")
    
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    finally:
        processing_active = False
        recognizer.disconnect_stream()
