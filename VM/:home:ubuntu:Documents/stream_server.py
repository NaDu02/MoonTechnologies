from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import face_recognition
import numpy as np
import cv2
import base64
import os
import threading
import time
import json
import sqlite3
from collections import deque
from datetime import datetime
from werkzeug.utils import secure_filename
import stripe
import paho.mqtt.client as mqtt
import threading
import time

# Konfiguration laden
def load_config():
    """Lädt Konfiguration aus config.json"""
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("config.json nicht gefunden!")
        return {}

config = load_config()
stripe.api_key = config.get('stripe', {}).get('secret_key', 'demo_key')
STRIPE_PUBLISHABLE_KEY = config.get('stripe', {}).get('publishable_key', 'demo_key')

# MQTT Configuration
MQTT_BROKER = "141.72.12.186"
MQTT_PORT = 1883
def init_mqtt():
    """MQTT Client initialisieren"""
    try:
        client = mqtt.Client()
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print(f"MQTT Client verbunden mit {MQTT_BROKER}:{MQTT_PORT}")
        return client
    except Exception as e:
        print(f"MQTT Verbindung fehlgeschlagen: {e}")
        return None

# Stripe Key Check
if stripe.api_key.startswith('sk_test_'):
    print("Stripe Test-Keys geladen")
elif stripe.api_key.startswith('sk_live_'):
    print("WARNUNG: Live Keys detected! Echter Geld-Modus!")
else:
    print("Demo-Modus: Keine gültigen Stripe Keys")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'face_recognition_secret'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

mqtt_client = init_mqtt()

def send_payment_result_to_esp(success, user_name=None):
    """Sendet Payment-Ergebnis an ESP32"""
    if not mqtt_client:
        print("MQTT Client nicht verfügbar")
        return
    
    try:
        result = "success" if success else "error"
        topic = "fay_node/payment/result"
        
        mqtt_client.publish(topic, result)
        print(f"🚀 MQTT an ESP32 gesendet: {topic} = {result}")
        
        if user_name:
            print(f"   User: {user_name}")
            
    except Exception as e:
        print(f"MQTT Send-Fehler: {e}")

# Globale Variablen
known_face_encodings = []
known_face_names = []
current_recognition = {'face_recognized': False, 'user_name': 'Warte...', 'confidence': 0}
processing_queue = deque(maxlen=3)
processing_active = True

# KORRIGIERTE Product Integration Variablen
detected_products_file = "/home/ubuntu/Documents/product_recog/detected_products.txt"
current_detected_products = []

def load_detected_products():
    """Lädt erkannte Produkte aus der Textdatei (ALLE PRODUKTE, keine Zeitstempel-Filterung)"""
    global current_detected_products
    
    try:
        if os.path.exists(detected_products_file):
            print(f"📦 Lade ALLE Produktdaten aus: {detected_products_file}")
            
            with open(detected_products_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            products = []
            total_value = 0.0
            
            # Parse jede Zeile nach dem Format: Zeitstempel | Produktname | Preis(€) | Konfidenz(%) | Model-ID
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                
                # Skip header lines und leere Zeilen
                if not line or line.startswith('===') or line.startswith('Format:') or line.startswith('===='):
                    continue
                
                # Parse die Produktzeile
                parts = [part.strip() for part in line.split('|')]
                if len(parts) >= 4:
                    try:
                        timestamp_str = parts[0]
                        product_name = parts[1]
                        if product_name == "Product_0":
                            product_name = "Baeren Marken Milch"
                        elif product_name == "Product_1":
                            product_name = "Vitalis Muesli 500g"
                        price_str = parts[2].replace('€', '').strip()
                        confidence_str = parts[3].replace('%', '').strip()
                        model_id = parts[4] if len(parts) > 4 else 'Unknown'
                        
                        # Konvertiere Werte
                        price_euro = float(price_str)
                        confidence_percent = float(confidence_str)
                        
                        # Erstelle Produktobjekt
                        product = {
                            'name': product_name,
                            'price_euro': price_euro,
                            'confidence_percent': confidence_percent,
                            'timestamp': timestamp_str,
                            'model_id': model_id,
                            'id': f"{product_name}_{timestamp_str.replace(' ', '_').replace(':', '_')}"
                        }
                        
                        products.append(product)
                        total_value += price_euro
                        
                        print(f"   ✅ Geladen: {product_name} - {price_euro}€ ({confidence_percent}%) [{timestamp_str}]")
                        
                    except (ValueError, IndexError) as e:
                        print(f"   ⚠️  Fehler beim Parsen Zeile {line_num}: {line} -> {e}")
                        continue
            
            current_detected_products = products
            
            result = {
                'products': products,
                'total_value': round(total_value, 2),
                'product_count': len(products),
                'last_updated': datetime.now().isoformat(),
                'status': 'loaded',
                'source_file': detected_products_file
            }
            
            print(f"📦 {len(products)} Produkte geladen, Gesamtwert: {total_value:.2f}€")
            return result
            
        else:
            print(f"❌ Produktdatei nicht gefunden: {detected_products_file}")
            current_detected_products = []
            return {'products': [], 'total_value': 0, 'product_count': 0, 'status': 'file_not_found'}
            
    except Exception as e:
        print(f"❌ Fehler beim Laden der Produktdaten: {e}")
        current_detected_products = []
        return {'products': [], 'total_value': 0, 'product_count': 0, 'status': 'error', 'error': str(e)}

def clear_detected_products():
    """Löscht erkannte Produkte"""
    global current_detected_products
    
    try:
        current_detected_products = []
        
        # Schreibe leere Datei mit Header
        empty_content = """=== ERKANNTE PRODUKTE MIT PREISEN ===
Format: Zeitstempel | Produktname | Preis(€) | Konfidenz(%) | Model-ID
================================================================================
"""
        
        with open(detected_products_file, 'w', encoding='utf-8') as f:
            f.write(empty_content)
        
        print("🔄 Erkannte Produkte gelöscht")
        return True
    except Exception as e:
        print(f"Fehler beim Löschen der Produktdaten: {e}")
        return False

def get_current_cart_summary():
    """Gibt eine Zusammenfassung des aktuellen Warenkorbs zurück"""
    if not current_detected_products:
        load_detected_products()
    
    # Gruppiere identische Produkte
    product_groups = {}
    for product in current_detected_products:
        key = product['name']
        if key not in product_groups:
            product_groups[key] = {
                'name': product['name'],
                'price_euro': product['price_euro'],
                'count': 0,
                'total_price': 0.0,
                'avg_confidence': 0.0,
                'timestamps': []
            }
        
        product_groups[key]['count'] += 1
        product_groups[key]['total_price'] += product['price_euro']
        product_groups[key]['avg_confidence'] += product['confidence_percent']
        product_groups[key]['timestamps'].append(product['timestamp'])
    
    # Berechne Durchschnitte
    for group in product_groups.values():
        group['avg_confidence'] = group['avg_confidence'] / group['count']
    
    return {
        'groups': list(product_groups.values()),
        'total_items': len(current_detected_products),
        'unique_products': len(product_groups),
        'total_value': sum(p['price_euro'] for p in current_detected_products)
    }

# Datenbank Setup
def init_database():
    """Erstellt User- und Payment-Tabellen"""
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    # Users Tabelle
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            email TEXT,
            stripe_customer_id TEXT,
            payment_enabled BOOLEAN DEFAULT FALSE,
            default_amount INTEGER DEFAULT 500,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Payments Tabelle
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            stripe_payment_id TEXT,
            status TEXT DEFAULT 'pending',
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_name) REFERENCES users (name)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Datenbank initialisiert")

def get_user_payment_info(name):
    """Holt Payment-Info für einen User"""
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE name = ?', (name,))
    user = cursor.fetchone()
    
    conn.close()
    return user

def get_user_default_amount(user_name):
    """Holt Standard-Betrag für User"""
    user = get_user_payment_info(user_name)
    return (user[5] / 100) if user else 5.00  # Standard 5€

def create_payment_for_user(user_name, confidence, amount_cents=None):
    """Erstellt automatisch Payment wenn User erkannt wird"""
    try:
        # User-Info holen
        user = get_user_payment_info(user_name)
        if not user:
            print(f"User {user_name} nicht in Datenbank gefunden")
            return None
            
        user_id, name, email, stripe_customer_id, payment_enabled, default_amount, created_at = user
        
        if not payment_enabled:
            print(f"Payment für {user_name} nicht aktiviert")
            return None
            
        # Betrag festlegen
        final_amount = amount_cents or default_amount or 500
        
        # ECHTE STRIPE INTEGRATION
        if stripe.api_key and stripe.api_key.startswith('sk_test_') and stripe_customer_id.startswith('cus_'):
            try:
                print(f"Erstelle ECHTES Stripe Payment...")
                print(f"   Customer: {stripe_customer_id}")
                print(f"   User: {user_name}")
                print(f"   Betrag: {final_amount/100}€")
                
                # 1. Payment Methods des Customers abrufen
                payment_methods = stripe.PaymentMethod.list(
                    customer=stripe_customer_id,
                    type="card"
                )
                
                if not payment_methods.data:
                    print(f"Keine Payment Method für Customer {stripe_customer_id}!")
                    return None
                
                # 2. Erste Payment Method verwenden
                payment_method_id = payment_methods.data[0].id
                print(f"Verwende Payment Method: {payment_method_id}")
                
                # 3. Payment Intent erstellen
                payment_intent = stripe.PaymentIntent.create(
                    amount=final_amount,
                    currency='eur',
                    customer=stripe_customer_id,
                    payment_method=payment_method_id,
                    confirm=True,
                    automatic_payment_methods={
                        'enabled': True,
                        'allow_redirects': 'never'
                    },
                    description=f'Face Recognition Payment - {user_name}',
                    metadata={
                        'user_name': user_name,
                        'confidence': str(confidence),
                        'recognition_time': datetime.now().isoformat(),
                        'system': 'face_recognition_auto'
                    }
                )
                
                payment_id = payment_intent.id
                status = payment_intent.status
                
                print(f"ECHTES STRIPE PAYMENT ERFOLGREICH!")
                print(f"   Payment ID: {payment_id}")
                print(f"   Status: {status}")
                
                if status == 'succeeded':
                    print(f"PAYMENT ERFOLGREICH ABGESCHLOSSEN!")
                
            except stripe.error.StripeError as e:
                print(f"Stripe API Fehler: {e}")
                payment_id = f'stripe_error_{int(time.time())}'
                status = 'stripe_error'
                
        else:
            print(f"Demo-Modus: Stripe Key = {stripe.api_key}")
            payment_id = f'demo_{int(time.time())}'
            status = 'demo_completed'
        
        # Payment in DB speichern
        conn = sqlite3.connect('face_payments.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO payments (user_name, amount, status, confidence, stripe_payment_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_name, final_amount, status, confidence, payment_id))
        
        conn.commit()
        conn.close()
        
        # MQTT an ESP32 senden
        payment_success = status in ['succeeded', 'demo_completed']
        send_payment_result_to_esp(payment_success, user_name)
        
        return {
            'id': payment_id,
            'amount': final_amount,
            'status': status
        }
        
    except Exception as e:
        print(f"Payment-Fehler für {user_name}: {e}")
        send_payment_result_to_esp(False, user_name)
        return None

def load_known_faces():
    """Lädt bekannte Gesichter einmalig beim Start"""
    global known_face_encodings, known_face_names
    
    known_face_encodings = []
    known_face_names = []
    
    os.makedirs('known_faces', exist_ok=True)
    for filename in os.listdir('known_faces'):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            try:
                image_path = os.path.join('known_faces', filename)
                image = face_recognition.load_image_file(image_path)
                encodings = face_recognition.face_encodings(image)
                
                if encodings:
                    known_face_encodings.append(encodings[0])
                    name = os.path.splitext(filename)[0]
                    known_face_names.append(name)
                    print(f"Geladen: {name}")
            except Exception as e:
                print(f"Fehler bei {filename}: {e}")

# Werte senden
def mqtt_confidence_sender():
    """Sendet alle 30 Sekunden Confidence-Updates"""
    while processing_active:
        try:
            # Confidence aus DB holen und senden
            with app.test_client() as client:
                response = client.get('/metrics')
                if response.status_code == 200:
                    print("📡 Automatischer MQTT-Send")
            time.sleep(30)  # 30 Sekunden warten
        except Exception as e:
            print(f"Auto-MQTT Fehler: {e}")
            time.sleep(30)

def background_processor():
    """Hintergrund-Thread für Gesichtserkennung MIT Product-Anzeige"""
    global current_recognition, processing_active, current_detected_products
    last_payment_trigger = {}
    last_product_check = 0
    
    while processing_active:
        try:
            if processing_queue:
                image_b64 = processing_queue.popleft()
                result = process_frame_fast(image_b64)
                current_recognition = result
                
                # Alle 5 Sekunden Produktdaten neu laden
                current_time = time.time()
                if current_time - last_product_check > 5:
                    product_data = load_detected_products()
                    if product_data['product_count'] > 0:
                        print(f"🛒 Warenkorb-Update: {product_data['product_count']} Produkte, {product_data['total_value']}€")
                        
                        # Sende Warenkorb-Update an alle Clients
                        socketio.emit('cart_update', {
                            'products': current_detected_products,
                            'total_value': product_data['total_value'],
                            'product_count': product_data['product_count'],
                            'summary': get_current_cart_summary(),
                            'timestamp': datetime.now().strftime("%H:%M:%S")
                        })
                    
                    last_product_check = current_time
                
                # PAYMENT DIALOG TRIGGER bei erfolgreicher Gesichtserkennung
                if result['face_recognized'] and result['confidence'] > 0.6:
                    user_name = result['user_name']
                    confidence = result['confidence']
                    
                    # Nur alle 30 Sekunden Payment-Dialog für gleiche Person
                    if user_name not in last_payment_trigger or \
                       current_time - last_payment_trigger[user_name] > 30:
                        
                        print(f"FACE ERKANNT: {user_name}! ({confidence:.1%})")
                        
                        # Aktuelle Warenkorb-Daten holen
                        product_data = load_detected_products()
                        
                        # Standard Payment-Dialog senden
                        socketio.emit('payment_dialog', {
                            'user_name': user_name,
                            'confidence': confidence,
                            'default_amount': get_user_default_amount(user_name),
                            'timestamp': datetime.now().strftime("%H:%M:%S"),
                            'source': 'face_recognition',
                            'has_products': product_data['product_count'] > 0,
                            'products': current_detected_products,
                            'total_value': product_data['total_value'],
                            'summary': get_current_cart_summary()
                        })
                        
                        # Zusätzlich: Product Payment Info senden (falls Produkte erkannt)
                        if current_detected_products:
                            summary = get_current_cart_summary()
                            
                            print(f"💰 Erkannte Produkte verfügbar: {summary['unique_products']} verschiedene Produkte")
                            
                            socketio.emit('products_available', {
                                'user_name': user_name,
                                'products': current_detected_products,
                                'total_value': product_data['total_value'],
                                'product_count': product_data['product_count'],
                                'summary': summary,
                                'message': f'{summary["unique_products"]} verschiedene Produkte - {product_data["total_value"]}€',
                                'timestamp': datetime.now().strftime("%H:%M:%S")
                            })
                        
                        last_payment_trigger[user_name] = current_time
                
                # Ergebnis an alle Clients senden
                socketio.emit('recognition_result', result)
            else:
                time.sleep(0.05)
        except Exception as e:
            print(f"Processing error: {e}")
            time.sleep(0.1)

def process_frame_fast(image_b64):
    """Optimierte Gesichtserkennung mit Bounding Boxes"""
    try:
        # Base64 zu Image
        image_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        # Frame für bessere Performance verkleinern
        small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
        
        # Gesichtserkennung
        face_locations = face_recognition.face_locations(rgb_small_frame, model='hog')
        
        if not face_locations:
            result = {
                'face_recognized': False,
                'user_name': 'Suche...',
                'confidence': 0,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'face_count': 0,
                'faces': []
            }
        else:
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            faces_data = []
            best_match = None
            
            for i, face_encoding in enumerate(face_encodings):
                face_location = face_locations[i]
                
                # Koordinaten zurück auf Original-Größe skalieren
                top, right, bottom, left = face_location
                scale_factor = 2.0
                top = int(top * scale_factor)
                right = int(right * scale_factor)
                bottom = int(bottom * scale_factor)
                left = int(left * scale_factor)
                
                face_info = {
                    'box': {
                        'left': left,
                        'top': top,
                        'right': right,
                        'bottom': bottom
                    },
                    'name': 'Unbekannt',
                    'confidence': 0
                }
                
                if len(known_face_encodings) > 0:
                    matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.45)
                    
                    if True in matches:
                        face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                        best_match_index = np.argmin(face_distances)
                        
                        if matches[best_match_index]:
                            name = known_face_names[best_match_index]
                            confidence = 1 - face_distances[best_match_index]
                            
                            face_info['name'] = name
                            face_info['confidence'] = confidence
                            
                            # Bester Match für Haupt-Panel
                            if not best_match or confidence > best_match['confidence']:
                                best_match = {
                                    'name': name,
                                    'confidence': confidence
                                }
                
                faces_data.append(face_info)
            
            # Ergebnis zusammenstellen
            if best_match:
                result = {
                    'face_recognized': True,
                    'user_name': best_match['name'],
                    'confidence': best_match['confidence'],
                    'timestamp': datetime.now().strftime("%H:%M:%S"),
                    'face_count': len(face_locations),
                    'faces': faces_data
                }
            else:
                result = {
                    'face_recognized': False,
                    'user_name': 'Unbekannt',
                    'confidence': 0,
                    'timestamp': datetime.now().strftime("%H:%M:%S"),
                    'face_count': len(face_locations),
                    'faces': faces_data
                }

        # SOFORT Live Confidence senden wenn sich was ändert
        global current_recognition
        old_confidence = current_recognition.get('confidence', 0)
        current_recognition = result
        
        # Live Confidence sofort senden bei Änderung
        new_confidence = result.get('confidence', 0) * 100
        if abs(new_confidence - old_confidence * 100) > 1:  # Nur bei Änderung > 1%
            if mqtt_client:
                try:
                    mqtt_client.publish("facerecog/current_confidence", round(new_confidence, 1))
                    print(f"📡 Live Confidence: {round(new_confidence, 1)}%")
                except Exception as e:
                    print(f"MQTT Live Send-Fehler: {e}")
        
        return result
        
    except Exception as e:
        print(f"Processing error: {e}")
        return {
            'face_recognized': False,
            'user_name': 'Fehler',
            'confidence': 0,
            'timestamp': datetime.now().strftime("%H:%M:%S"),
            'face_count': 0,
            'faces': []
        }

# Config API Endpoint
@app.route('/config')
def show_config():
    """Zeigt aktuelle Konfiguration"""
    key_type = "unknown"
    if stripe.api_key:
        if stripe.api_key.startswith('sk_test_'):
            key_type = "test"
        elif stripe.api_key.startswith('sk_live_'):
            key_type = "live"
        elif stripe.api_key == 'demo_key':
            key_type = "demo"
    
    return jsonify({
        'stripe_configured': stripe.api_key != 'demo_key',
        'stripe_key_type': key_type,
        'stripe_key_prefix': stripe.api_key[:10] + "..." if stripe.api_key != 'demo_key' else 'demo_key',
        'database_exists': os.path.exists('face_payments.db'),
        'config_file_exists': os.path.exists('config.json'),
        'known_faces': known_face_names,
        'known_faces_count': len(known_face_names)
    })

# KORRIGIERTE Product API Endpoints

@app.route('/api/detected_products')
def get_detected_products():
    """Gibt alle erkannten Produkte zurück"""
    data = load_detected_products()
    summary = get_current_cart_summary()
    
    result = {
        **data,
        'summary': summary,
        'grouped_products': summary['groups']
    }
    
    return jsonify(result)

@app.route('/api/clear_products', methods=['POST'])
def clear_products():
    """Löscht alle erkannten Produkte"""
    success = clear_detected_products()
    
    if success:
        # Clients informieren
        socketio.emit('products_cleared', {
            'message': 'Alle Produkte gelöscht',
            'products': [],
            'total_value': 0
        })
        
        return jsonify({
            'success': True,
            'message': 'Alle erkannten Produkte gelöscht'
        })
    else:
        return jsonify({'error': 'Fehler beim Löschen'}), 500

@app.route('/api/pay_for_products', methods=['POST'])
def pay_for_products():
    """Bezahlt für alle erkannten Produkte"""
    try:
        data = request.get_json()
        user_name = data.get('user_name', 'Guest')
        
        # Aktuelle Produkte laden
        product_data = load_detected_products()
        products = product_data.get('products', [])
        
        if not products:
            return jsonify({'error': 'Keine Produkte zum Bezahlen'}), 400
        
        # Gesamtsumme berechnen
        total_amount = product_data.get('total_value', 0)
        product_names = [p['name'] for p in products]
        summary = get_current_cart_summary()
        
        print(f"💳 PRODUCT PAYMENT: {user_name} bezahlt {total_amount}€ für {len(products)} Produkte")
        print(f"   Produkte: {summary['unique_products']} verschiedene Typen")
        
        # Payment über bestehende Logik erstellen
        payment = create_payment_for_user(
            user_name=user_name,
            confidence=0.95,  # Hohe Konfidenz für manuelle Zahlung
            amount_cents=int(total_amount * 100)
        )
        
        if payment:
            # Payment-Erfolg an alle Clients senden
            socketio.emit('payment_triggered', {
                'user_name': user_name,
                'amount': total_amount,
                'payment_id': payment['id'],
                'status': 'success',
                'source': 'product_payment',
                'products': products,
                'product_names': product_names,
                'product_count': len(products),
                'summary': summary,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'message': f'Payment für {len(products)} Produkte erfolgreich'
            })
            
            # Erkannte Produkte nach erfolgreicher Zahlung löschen
            clear_detected_products()
            
            return jsonify({
                'success': True,
                'payment_id': payment['id'],
                'amount': total_amount,
                'products_count': len(products),
                'products': product_names,
                'summary': summary,
                'message': f'Payment für {len(products)} Produkte erfolgreich'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Payment für {user_name} konnte nicht erstellt werden'
            }), 400
            
    except Exception as e:
        print(f"Product Payment Fehler: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/product_status')
def get_product_status():
    """Gibt Status der Produkterkennung zurück"""
    data = load_detected_products()
    summary = get_current_cart_summary()
    
    return jsonify({
        'has_products': len(data.get('products', [])) > 0,
        'product_count': data.get('product_count', 0),
        'total_value': data.get('total_value', 0),
        'products': data.get('products', []),
        'grouped_products': summary['groups'],
        'unique_products': summary['unique_products'],
        'last_updated': data.get('last_updated'),
        'status': data.get('status', 'unknown'),
        'source_file': detected_products_file,
        'summary': summary
    })

# Payment API Endpoints
@app.route('/payment/setup/<name>', methods=['POST'])
def setup_user_payment(name):
    """Automatisches Customer Setup"""
    try:
        data = request.get_json()
        email = data.get('email', f'{name}@example.com')
        amount = data.get('amount', 500)
        
        # ECHTEN STRIPE CUSTOMER ERSTELLEN
        if stripe.api_key and stripe.api_key.startswith('sk_test_'):
            try:
                print(f"Erstelle automatisch Stripe Customer für {name}...")
                
                # Customer erstellen
                customer = stripe.Customer.create(
                    email=email,
                    name=name,
                    description=f'Face Recognition User - {name}'
                )
                
                customer_id = customer.id
                print(f"Stripe Customer erstellt: {customer_id}")
                
            except Exception as e:
                print(f"Stripe Customer Fehler: {e}")
                customer_id = f'demo_customer_{name}'
        else:
            customer_id = f'demo_customer_{name}'
        
        # User in DB speichern
        conn = sqlite3.connect('face_payments.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users (name, email, stripe_customer_id, payment_enabled, default_amount)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, email, customer_id, True, amount))
        
        conn.commit()
        conn.close()
        
        print(f"User {name} automatisch in DB gespeichert mit Customer {customer_id}")
        
        return jsonify({
            'success': True,
            'customer_id': customer_id,
            'message': f'Customer für {name} erstellt!',
            'amount': amount / 100,
            'stripe_dashboard_url': f'https://dashboard.stripe.com/test/customers/{customer_id}' if customer_id.startswith('cus_') else None,
            'next_step': 'Jetzt Payment Method im Stripe Dashboard hinzufügen'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/payment/add-card/<name>', methods=['POST'])
def add_payment_method(name):
    """Erstellt Setup Intent für sichere Payment Method Erfassung"""
    try:
        user = get_user_payment_info(name)
        if not user:
            return jsonify({'error': f'User {name} nicht gefunden'}), 404
            
        customer_id = user[3]  # stripe_customer_id
        
        if not customer_id.startswith('cus_'):
            return jsonify({'error': 'Kein echter Stripe Customer'}), 400
        
        print(f"Erstelle Setup Intent für {name}...")
        
        # Setup Intent für sichere Kartenerfassung erstellen
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=['card'],
            usage='off_session'
        )
        
        print(f"Setup Intent erstellt: {setup_intent.id}")
        
        return jsonify({
            'success': True,
            'message': f'Setup Intent für {name} erstellt - Payment Method manuell im Dashboard hinzufügen',
            'setup_intent_id': setup_intent.id,
            'customer_id': customer_id,
            'stripe_dashboard_url': f'https://dashboard.stripe.com/test/customers/{customer_id}',
            'instruction': 'Gehe zum Stripe Dashboard → Customer öffnen → Payment methods → Add payment method → Karte 4242424242424242 eingeben'
        })
        
    except Exception as e:
        print(f"Setup Intent Fehler: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/payment/setup-complete/<name>', methods=['POST'])
def complete_payment_setup(name):
    """Erstellt Customer + Anleitung für manuelle Payment Method"""
    try:
        data = request.get_json()
        email = data.get('email', f'{name}@example.com')
        amount = data.get('amount', 500)
        
        if stripe.api_key and stripe.api_key.startswith('sk_test_'):
            print(f"Starte Setup für {name}...")
            
            # Customer erstellen
            customer = stripe.Customer.create(
                email=email, 
                name=name,
                description=f'Face Recognition User - {name}'
            )
            customer_id = customer.id
            print(f"Customer erstellt: {customer_id}")
            
            # DB updaten
            conn = sqlite3.connect('face_payments.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (name, email, stripe_customer_id, payment_enabled, default_amount)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, email, customer_id, True, amount))
            conn.commit()
            conn.close()
            print(f"DB aktualisiert für {name}")
            
            return jsonify({
                'success': True,
                'message': f'Customer für {name} erstellt! Payment Method manuell hinzufügen.',
                'customer_id': customer_id,
                'amount': amount / 100,
                'stripe_dashboard_url': f'https://dashboard.stripe.com/test/customers/{customer_id}',
                'instruction': 'Stripe Dashboard → Customer → Payment methods → Add payment method → Karte: 4242424242424242'
            })
        else:
            return jsonify({'error': 'Stripe Test Keys erforderlich'}), 400
            
    except Exception as e:
        print(f"Setup Fehler: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/payment/check-cards/<name>')
def check_payment_methods(name):
    """Prüft ob User Payment Methods hat"""
    try:
        user = get_user_payment_info(name)
        if not user:
            return jsonify({'error': f'User {name} nicht gefunden'}), 404
            
        customer_id = user[3]
        
        if not customer_id or not customer_id.startswith('cus_'):
            return jsonify({'has_cards': False, 'message': 'Kein Stripe Customer'})
        
        # Payment Methods prüfen
        try:
            payment_methods = stripe.PaymentMethod.list(
                customer=customer_id,
                type="card"
            )
            
            cards = []
            for pm in payment_methods.data:
                if pm.card:
                    cards.append({
                        'id': pm.id,
                        'last4': pm.card.last4,
                        'brand': pm.card.brand,
                        'exp_month': pm.card.exp_month,
                        'exp_year': pm.card.exp_year
                    })
            
            return jsonify({
                'has_cards': len(cards) > 0,
                'card_count': len(cards),
                'cards': cards,
                'customer_id': customer_id,
                'ready_for_payments': len(cards) > 0
            })
            
        except stripe.error.StripeError as e:
            return jsonify({
                'has_cards': False,
                'message': f'Stripe Fehler: {str(e)}',
                'customer_id': customer_id
            })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/payment/history/<name>')
def get_payment_history(name):
    """Payment-Historie für einen User"""
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM payments WHERE user_name = ? ORDER BY created_at DESC LIMIT 20
    ''', (name,))
    
    payments = cursor.fetchall()
    conn.close()
    
    return jsonify({
        'user': name,
        'total_payments': len(payments),
        'total_amount': sum(p[2] for p in payments) / 100,
        'payments': [
            {
                'id': p[0],
                'amount': p[2] / 100,
                'status': p[4],
                'confidence': p[5],
                'date': p[6],
                'stripe_payment_id': p[3]
            } for p in payments
        ]
    })

@app.route('/payment/users')
def get_payment_users():
    """Alle Payment-User auflisten"""
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
    users = cursor.fetchall()
    
    conn.close()
    
    return jsonify({
        'users': [
            {
                'name': u[1],
                'email': u[2],
                'payment_enabled': bool(u[4]),
                'default_amount': u[5] / 100,
                'stripe_customer_id': u[3],
                'created_at': u[6]
            } for u in users
        ]
    })

@app.route('/payment/disable/<name>', methods=['POST'])
def disable_user_payment(name):
    """Payment für User deaktivieren"""
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET payment_enabled = FALSE WHERE name = ?', (name,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': f'Payment für {name} deaktiviert'})

# Bestehende REST API Endpoints
@app.route('/health')
def health_check():
    """Server-Status für headless_capture.py"""
    product_data = load_detected_products()
    
    return jsonify({
        'status': 'running',
        'known_faces': len(known_face_names),
        'faces_loaded': known_face_names,
        'payment_enabled': True,
        'stripe_configured': stripe.api_key != 'demo_key',
        'detected_products': len(current_detected_products),
        'cart_total': product_data.get('total_value', 0),
        'cart_items': product_data.get('product_count', 0),
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route('/add_face', methods=['POST'])
def add_face():
    """Neues Gesicht hinzufügen"""
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
        
        filename = secure_filename(f"{name}.jpg")
        filepath = os.path.join('known_faces', filename)
        
        image_file.save(filepath)
        
        try:
            image = face_recognition.load_image_file(filepath)
            encodings = face_recognition.face_encodings(image)
            
            if not encodings:
                os.remove(filepath)
                return jsonify({'error': 'No face detected in image'}), 400
            
            load_known_faces()
            
            return jsonify({
                'success': True,
                'message': f'Face for {name} added successfully',
                'name': name,
                'total_faces': len(known_face_names),
                'faces_loaded': known_face_names
            })
            
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': f'Failed to process image: {str(e)}'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/list_faces')
def list_faces():
    """Alle bekannten Gesichter auflisten"""
    return jsonify({
        'faces': known_face_names,
        'count': len(known_face_names)
    })

@app.route('/delete_face/<name>')
def delete_face(name):
    """Gesicht löschen"""
    try:
        extensions = ['.jpg', '.jpeg', '.png']
        deleted = False
        
        for ext in extensions:
            filepath = os.path.join('known_faces', f"{name}{ext}")
            if os.path.exists(filepath):
                os.remove(filepath)
                deleted = True
                break
        
        if deleted:
            load_known_faces()
            return jsonify({
                'success': True,
                'message': f'Face {name} deleted successfully',
                'remaining_faces': known_face_names
            })
        else:
            return jsonify({'error': f'Face {name} not found'}), 404
            
    except Exception as e:
        return jsonify({'error': f'Failed to delete face: {str(e)}'}), 500

# Web Interface
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/payment-setup')
def payment_setup():
    return render_template('payment_setup.html', faces=known_face_names)

@app.route('/metrics')
def get_metrics():
    """Live-Metriken für OpenHAB"""
    global current_recognition
    
    # Letzte 10 Confidence-Werte sammeln
    conn = sqlite3.connect('face_payments.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT confidence FROM payments 
        WHERE confidence > 0 
        ORDER BY created_at DESC 
        LIMIT 10
    ''')
    recent_confidences = [row[0] for row in cursor.fetchall()]
    
    # Payments heute
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT COUNT(*) FROM payments 
        WHERE DATE(created_at) = ? AND status != 'failed'
    ''', (today,))
    payments_today = cursor.fetchone()[0]
    
    conn.close()
    
    avg_confidence = sum(recent_confidences) / len(recent_confidences) if recent_confidences else 0
    
    # *** HIER DEN MQTT-PUBLISH HINZUFÜGEN: ***
    if mqtt_client:
        try:
            # Average Confidence an OpenHAB senden (0-100 scale)
            mqtt_client.publish("facerecog/average_confidence", round(avg_confidence * 100, 1))
            print(f"📡 MQTT gesendet: average_confidence = {round(avg_confidence * 100, 1)}")
            known_faces_count = len(known_face_names)
            mqtt_client.publish("facerecog/known_faces", known_faces_count)
        except Exception as e:
            print(f"MQTT Send-Fehler: {e}")
    
    product_data = load_detected_products()
    
    return jsonify({
        'current_confidence': current_recognition.get('confidence', 0) * 100,
        'average_confidence': round(avg_confidence * 100, 1),
        'payments_today': payments_today,
        'queue_size': len(processing_queue),
        'face_recognized': current_recognition.get('face_recognized', False),
        'current_user': current_recognition.get('user_name', 'None'),
        'cart_items': product_data.get('product_count', 0),
        'cart_value': product_data.get('total_value', 0),
        'timestamp': datetime.now().isoformat()
    })

# Socket.IO Events
@socketio.on('connect')
def handle_connect():
    print("Client verbunden")
    emit('recognition_result', current_recognition)
    
    # Sende aktuellen Warenkorb-Status
    product_data = load_detected_products()
    if product_data['product_count'] > 0:
        emit('cart_update', {
            'products': current_detected_products,
            'total_value': product_data['total_value'],
            'product_count': product_data['product_count'],
            'summary': get_current_cart_summary(),
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })

@socketio.on('disconnect')
def handle_disconnect():
    print("Client getrennt")

@socketio.on('video_frame')
def handle_video_frame(data):
    """Empfängt Video-Frames und verarbeitet sie asynchron"""
    if 'image' in data:
        if len(processing_queue) < processing_queue.maxlen:
            processing_queue.append(data['image'])
        
        emit('video_frame', {'image': data['image']}, broadcast=True)

@socketio.on('confirm_payment')
def handle_payment_confirmation(data):
    """Verarbeitet bestätigte Payments mit gewähltem Betrag"""
    try:
        user_name = data.get('user_name')
        amount_euros = float(data.get('amount', 5.0))
        confidence = float(data.get('confidence', 0))
        
        amount_cents = int(amount_euros * 100)
        
        print(f"Payment bestätigt: {user_name} - {amount_euros}€")
        
        payment = create_payment_for_user(user_name, confidence, amount_cents)
        
        if payment:
            socketio.emit('payment_triggered', {
                'user_name': user_name,
                'amount': payment['amount'] / 100,
                'payment_id': payment['id'],
                'status': 'success',
                'confidence': confidence,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            print(f"Payment-Notification gesendet")
        else:
            socketio.emit('payment_triggered', {
                'user_name': user_name,
                'status': 'failed',
                'message': 'Payment nicht konfiguriert',
                'confidence': confidence
            })
            
    except Exception as e:
        print(f"Payment confirmation error: {e}")
        send_payment_result_to_esp(False, data.get('user_name', 'Unknown'))
        socketio.emit('payment_triggered', {
           'user_name': data.get('user_name', 'Unknown'),
           'status': 'failed',
           'message': str(e)
       })

@socketio.on('pay_for_products')
def handle_pay_for_products_socket(data):
    """Verarbeitet Product Payment via Socket.IO"""
    try:
        user_name = data.get('user_name', 'Guest')
        
        # Aktuelle Produkte laden
        product_data = load_detected_products()
        products = product_data.get('products', [])
        
        if not products:
            emit('payment_result', {
                'success': False,
                'message': 'Keine Produkte zum Bezahlen'
            })
            return
        
        total_amount = product_data.get('total_value', 0)
        summary = get_current_cart_summary()
        
        # Payment erstellen
        payment = create_payment_for_user(user_name, 0.95, int(total_amount * 100))
        
        if payment:
            # Payment-Notification an alle senden
            socketio.emit('payment_triggered', {
                'user_name': user_name,
                'amount': total_amount,
                'payment_id': payment['id'],
                'status': 'success',
                'source': 'product_payment_socket',
                'products': products,
                'summary': summary,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            
            # Produkte löschen
            clear_detected_products()
            
            emit('payment_result', {
                'success': True,
                'payment_id': payment['id'],
                'amount': total_amount,
                'message': 'Product Payment erfolgreich'
            })
        else:
            emit('payment_result', {
                'success': False,
                'message': 'Payment konnte nicht erstellt werden'
            })
            
    except Exception as e:
        print(f"Socket Product Payment Fehler: {e}")
        emit('payment_result', {
            'success': False,
            'message': str(e)
        })

@socketio.on('request_product_status')
def handle_request_product_status():
    """Client fordert aktuellen Warenkorb-Status an"""
    product_data = load_detected_products()
    summary = get_current_cart_summary()
    
    emit('product_status', {
        'products': current_detected_products,
        'total_value': product_data['total_value'],
        'product_count': product_data['product_count'],
        'summary': summary,
        'status': product_data['status']
    })

@socketio.on('clear_products')
def handle_clear_products_socket():
    """Warenkorb via Socket.IO leeren"""
    success = clear_detected_products()
    
    if success:
        socketio.emit('products_cleared', {
            'message': 'Warenkorb geleert',
            'products': [],
            'total_value': 0
        }, broadcast=True)
        
        emit('clear_result', {'success': True})
    else:
        emit('clear_result', {'success': False, 'message': 'Fehler beim Leeren'})

if __name__ == '__main__':
    print("Starte Face Recognition Payment Server...")
    init_database()
    load_known_faces()
    print(f"{len(known_face_names)} Gesichter geladen: {known_face_names}")
    
    # Product Integration initialisieren
    product_status = load_detected_products()
    print(f"Product Integration: {product_status.get('product_count', 0)} Produkte geladen")
    print(f"Warenkorb-Gesamtwert: {product_status.get('total_value', 0):.2f}€")
    
    # Background-Processor starten
    processor_thread = threading.Thread(target=background_processor, daemon=True)
    processor_thread.start()

    # MQTT Client initialisieren
    mqtt_thread = threading.Thread(target=mqtt_confidence_sender, daemon=True)
    mqtt_thread.start()
    
    print(f"\nPayment-System aktiviert")
    print(f"Stripe-Modus: {'Test' if stripe.api_key.startswith('sk_test_') else 'Demo/Live'}")
    print("Server läuft auf http://0.0.0.0:5000")
    print("\nAPI Endpoints:")
    print("  GET  /config                        - System-Konfiguration")
    print("  GET  /health                        - Server-Status")
    print("  POST /add_face                      - Neues Gesicht hinzufügen")
    print("  GET  /list_faces                    - Alle Gesichter auflisten")
    print("  GET  /delete_face/<name>            - Gesicht löschen")
    print("  POST /payment/setup/<name>          - Customer Setup")
    print("  POST /payment/add-card/<name>       - Setup Intent erstellen")
    print("  POST /payment/setup-complete/<name> - Komplettes Setup")
    print("  GET  /payment/check-cards/<name>    - Payment Methods prüfen")
    print("  GET  /payment/history/<name>        - Payment Historie")
    print("  GET  /payment/users                 - Alle Payment-User")
    print("  POST /payment/disable/<name>        - Payment deaktivieren")
    print("  GET  /payment-setup                 - Payment Setup UI")
    print("  GET  /metrics                       - Live-Metriken für OpenHAB")
    print("\nWARENKORB API Endpoints:")
    print("  GET  /api/detected_products         - Erkannte Produkte anzeigen")
    print("  POST /api/clear_products            - Alle Produkte löschen")
    print("  POST /api/pay_for_products          - Für alle Produkte bezahlen")
    print("  GET  /api/product_status            - Produktstatus abfragen")
    print(f"\n💾 Produktdatei: '{detected_products_file}'")
    print("🔗 Verbindung zu Product Recognition System aktiv")
    print("🛒 ALLE PRODUKTE werden geladen (keine Zeitstempel-Filterung)")
    print("\nFace Recognition + Warenkorb Payments integriert!")
    print("\n🔗 MQTT Integration:")
    print(f"  Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Topic: fay_node/payment/result")
    print(f"  Status: {'✅ Verbunden' if mqtt_client else '❌ Fehler'}")
    
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    finally:
        processing_active = False
