# FAY & IVY - Moon Technologies
<img width="806" height="100" alt="Bildschirmfoto 2025-08-04 um 14 17 25" src="https://github.com/user-attachments/assets/4f5f99fb-0f09-43f7-a97c-8d7b75224d63" />


AI-basierte Self-Checkout-Lösung mit Gesichtserkennung (FAY) und Produkterkennung (IVY).

## Systemarchitektur

- **OpenStack VM**: Flask API-Server mit Face Recognition & Payment Processing
- **Raspberry Pi 4**: Kamera-Interface für Produkterkennung  
- **ESP32**: Hardware-Controller (LCD, LED, Joystick, Buzzer)
- **MQTT Broker**: Kommunikation zwischen allen Komponenten (Port 1883)
- **SQLite Datenbanken**: Face encodings & Product recognition data
- **Stripe Integration**: Test/Live Payment Processing

## Setup & Start

### 1. VM Server starten
```bash
cd VM/home/ubuntu/Documents
source face_recognition_env/bin/activate
python stream_server.py
```
**Server läuft auf**: `http://141.72.12.186:5000`

### 2. Raspberry Pi - Face Capture
```bash
cd Raspberry_Pi/Documents/face_recog
source ~/face_payment_env/bin/activate
python headless_capture.py  # Neue Gesichter hinzufügen
```

### 3. ESP32 - Hardware Interface
```bash
# ESPHome YAML flashen
esphome run esp32.yaml
```

### 4. Produkterkennung
```bash
cd VM/home/ubuntu/Documents/product_recog
# Produktbilder hinzufügen in product_models/ Ordner:
# product_models/0.jpg + optional 0.json (Name/Preis)
python product_recog.py
```

## API Endpoints

### Face Recognition
- `GET /` - Live Stream Interface mit Warenkorb
- `POST /add_face` - Gesicht hinzufügen
- `GET /list_faces` - Alle registrierten Gesichter
- `DELETE /delete_face/<n>` - Gesicht löschen

### Payment System  
- `POST /payment/setup/<n>` - Stripe Customer Setup
- `GET /payment/check-cards/<n>` - Payment Methods prüfen
- `GET /payment/history/<n>` - Zahlungshistorie
- `GET /payment-setup` - Payment Setup UI

### Product Recognition
- `GET /api/detected_products` - Warenkorb Status
- `POST /api/clear_products` - Warenkorb leeren
- `POST /api/pay_for_products` - Bezahlung auslösen

### System Status
- `GET /health` - Server Status
- `GET /config` - System Konfiguration  
- `GET /metrics` - Live Metriken für OpenHAB

## Tech Stack


- **Backend**: Python Flask + SocketIO
- **Payment**: Stripe API
- **IoT**: MQTT, ESPHome
- **Hardware**: ESP32, Raspberry Pi 4, Kamera-Module

![Architektur](https://github.com/user-attachments/assets/973c0e2c-83ee-4754-bd4a-3f0f5e97157a)

## ML-Modelle

<img width="655" height="341" alt="Bildschirmfoto 2025-08-04 um 14 16 10" src="https://github.com/user-attachments/assets/db79a89e-19ad-459e-ae63-a1a37da74e84" />

### Gesichtserkennung (FAY)
- **HOG (Histogram of Oriented Gradients)**: Gesichtsdetektion
- **68 Facial Landmarks**: Preprocessing und Gesichts-Rotation
- **Dlib ResNet CNN (Convolutional Neural Network)**: Vortrainiertes Modell für 128-dimensionale Face Encodings
- **Euklidische Distanzberechnung**: Gesichtsvergleich (99,38% Genauigkeit)

<img width="1135" height="571" alt="Bildschirmfoto 2025-08-04 um 14 07 38" src="https://github.com/user-attachments/assets/789c2f20-9564-482c-8a29-07970103405d" />

<img width="788" height="957" alt="image" src="https://github.com/user-attachments/assets/e55097d1-a349-41b1-8f66-ff8beed65375" />

### Produkterkennung (IVY)
- **SIFT (Scale-Invariant Feature Transform)**: Keypoint-Extraktion aus Produktbildern
- **RANSAC (Random Sample Consensus)**: Geometrische Validierung der Matches
- **Mindestens 8 aufeinanderfolgende Matches**: Erforderlich für sichere Produkterkennung


## Konfiguration

### ESPHome YAML (esp32.yaml)
Hardware-Konfiguration für ESP32:
- LCD Display (I2C, 20x4), RGB LED, Joystick, Passive Buzzer
- MQTT Integration für Statusmeldungen

### OpenHAB Integration (/etc/openhab/scripts/)
Automatisierte Script-Verwaltung:
- `face_recognition.py` - Startet Gesichtserkennung
- `product_recognition.py` - Startet Produkterkennung  
- `mqtt_monitor.py` - MQTT Topic Überwachung
- Automatisches Stoppen/Starten basierend auf MQTT-Triggern

### MQTT Topics
- `fay_node/payment/method` - Zahlungsmethoden-Auswahl (FACE_RECOGNITION/CASH)
- `fay_node/product/selection` - Produkterkennung-Trigger (PRODUCT_RECOGNITION)
- `fay_node/payment/result` - Payment-Ergebnis an ESP32 (success/error)
- `fay_node/status/mode` - System-Status
- `fay_node/events/face_started` - Face Recognition gestartet
- `fay_node/events/scan_started` - Product Scan gestartet
