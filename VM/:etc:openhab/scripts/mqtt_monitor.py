#!/usr/bin/env python3
"""
MQTT Monitor für Face/Product Recognition
Überwacht MQTT Topics und startet entsprechende Scripts
"""
import paho.mqtt.client as mqtt
import subprocess
import time
import logging
import signal
import sys

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/Documents/debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# MQTT Konfiguration
MQTT_BROKER = "141.72.12.186"
MQTT_PORT = 1883
TOPICS = {
    "fay_node/payment/method": "face_recognition",
    "fay_node/product/selection": "product_recognition"
}

# Script Pfade
SCRIPTS = {
    "face_recognition": "/etc/openhab/scripts/face_recognition.py",
    "product_recognition": "/etc/openhab/scripts/product_recognition.py"
}

PYTHON_PATH = "/home/ubuntu/Documents/face_recognition_env/bin/python3"

# Globale MQTT Client Variable
mqtt_client_global = None

def reset_mqtt_topics():
    """Setzt alle MQTT Topics zurück (leert sie)"""
    try:
        if mqtt_client_global and mqtt_client_global.is_connected():
            logger.info("=== MQTT TOPICS ZURÜCKSETZEN ===")
            
            # Alle relevanten Topics mit leeren retained Messages löschen
            topics_to_reset = [
                "fay_node/payment/method",
                "fay_node/product/selection",
                "fay_node/status/mode",
                "fay_node/events/face_started",
                "fay_node/events/scan_started"
            ]
            
            for topic in topics_to_reset:
                mqtt_client_global.publish(topic, "", retain=True)
                logger.info(f"Reset Topic: {topic}")
            
            # Kurz warten damit Messages gesendet werden
            time.sleep(0.5)
            logger.info("Alle MQTT Topics zurückgesetzt")
            
    except Exception as e:
        logger.error(f"Fehler beim MQTT Reset: {e}")

def stop_all_scripts():
    """Stoppt alle laufenden Recognition Scripts UND setzt MQTT Topics zurück"""
    try:
        logger.info("=== ALLE SCRIPTS STOPPEN + MQTT RESET ===")
        
        # 1. MQTT Topics zurücksetzen
        reset_mqtt_topics()
        
        # 2. Prozesse mit SUDO stoppen (für root-Prozesse)
        subprocess.run(["sudo", "pkill", "-f", "stream_server.py"], check=False)
        subprocess.run(["sudo", "pkill", "-f", "product_recog.py"], check=False)
        
        # 3. Falls das nicht reicht: SIGKILL verwenden
        time.sleep(2)
        subprocess.run(["sudo", "pkill", "-9", "-f", "stream_server.py"], check=False)
        subprocess.run(["sudo", "pkill", "-9", "-f", "product_recog.py"], check=False)
        
        logger.info("Alle Scripts gestoppt")
        
        # 4. Länger warten bis Ports frei sind
        time.sleep(5)
        
    except Exception as e:
        logger.error(f"Fehler beim Stoppen: {e}")

def start_script(script_type):
    """Startet ein bestimmtes Script"""
    try:
        if script_type not in SCRIPTS:
            logger.error(f"Unbekannter Script-Typ: {script_type}")
            return False
        
        script_path = SCRIPTS[script_type]
        logger.info(f"Starte {script_type}: {script_path}")
        
        # Script als Hintergrundprozess starten
        process = subprocess.Popen([
            "sudo", PYTHON_PATH, script_path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        time.sleep(3)  # Kurz warten
        
        # Prüfen ob Prozess läuft
        if script_type == "face_recognition":
            check_cmd = ["pgrep", "-f", "stream_server.py"]
        else:
            check_cmd = ["pgrep", "-f", "product_recog.py"]
        
        result = subprocess.run(check_cmd, capture_output=True)
        if result.returncode == 0:
            pid = result.stdout.decode().strip()
            logger.info(f"{script_type} erfolgreich gestartet (PID: {pid})")
            
            # Nach erfolgreichem Start: Status-Update senden
            send_status_update(script_type, "RUNNING")
            
            return True
        else:
            logger.error(f"{script_type} konnte nicht gestartet werden")
            return False
            
    except Exception as e:
        logger.error(f"Fehler beim Starten von {script_type}: {e}")
        return False

def send_status_update(script_type, status):
    """Sendet Status-Update nach erfolgreichem Script-Start"""
    try:
        if mqtt_client_global and mqtt_client_global.is_connected():
            if script_type == "face_recognition":
                mqtt_client_global.publish("fay_node/status/current_service", "FACE_RECOGNITION", retain=True)
                logger.info("Status: FACE_RECOGNITION service aktiv")
            elif script_type == "product_recognition":
                mqtt_client_global.publish("fay_node/status/current_service", "PRODUCT_RECOGNITION", retain=True)
                logger.info("Status: PRODUCT_RECOGNITION service aktiv")
                
    except Exception as e:
        logger.error(f"Fehler beim Status-Update: {e}")

def on_connect(client, userdata, flags, rc):
    """MQTT Verbindung hergestellt"""
    global mqtt_client_global
    mqtt_client_global = client
    
    if rc == 0:
        logger.info("MQTT verbunden")
        
        # Beim Connect erstmal alle Topics zurücksetzen
        reset_mqtt_topics()
        
        # Topics abonnieren
        for topic in TOPICS.keys():
            client.subscribe(topic)
            logger.info(f"Abonniert: {topic}")
            
        # Status senden dass Monitor aktiv ist
        client.publish("fay_node/status/monitor", "ACTIVE", retain=True)
        
    else:
        logger.error(f"MQTT Verbindung fehlgeschlagen: {rc}")

def on_message(client, userdata, msg):
    """MQTT Nachricht empfangen"""
    try:
        topic = msg.topic
        message = msg.payload.decode()
        
        # Leere Messages ignorieren (das sind unsere Reset-Messages)
        if not message or message.strip() == "":
            logger.debug(f"Ignoriere leere Message: {topic}")
            return
            
        logger.info(f"MQTT: {topic} = {message}")
        
        if topic == "fay_node/payment/method" and message == "FACE_RECOGNITION":
            logger.info("=== FACE RECOGNITION TRIGGER ===")
            stop_all_scripts()  # Inkludiert MQTT Reset
            start_script("face_recognition")
            
        elif topic == "fay_node/product/selection" and message == "PRODUCT_RECOGNITION":
            logger.info("=== PRODUCT RECOGNITION TRIGGER ===")
            stop_all_scripts()  # Inkludiert MQTT Reset
            start_script("product_recognition")
        
        else:
            logger.info(f"Ignoriere: {topic} = {message}")
            
    except Exception as e:
        logger.error(f"Fehler bei Nachricht: {e}")

def signal_handler(sig, frame):
    """Sauberes Beenden bei Ctrl+C"""
    logger.info("MQTT Monitor wird beendet...")
    
    # Beim Beenden auch Topics zurücksetzen
    reset_mqtt_topics()
    
    # Status auf INACTIVE setzen
    if mqtt_client_global and mqtt_client_global.is_connected():
        mqtt_client_global.publish("fay_node/status/monitor", "INACTIVE", retain=True)
        mqtt_client_global.publish("fay_node/status/current_service", "NONE", retain=True)
    
    stop_all_scripts()
    sys.exit(0)

def main():
    logger.info("=== MQTT MONITOR STARTET ===")
    
    # Signal Handler für Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # MQTT Client Setup
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        logger.info(f"Verbinde mit MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        
        logger.info("MQTT Monitor läuft - Ctrl+C zum Beenden")
        client.loop_forever()
        
    except Exception as e:
        logger.error(f"MQTT Monitor Fehler: {e}")
        reset_mqtt_topics()
        stop_all_scripts()

if __name__ == "__main__":
    main()
