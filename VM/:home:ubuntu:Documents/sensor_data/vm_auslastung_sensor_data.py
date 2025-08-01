#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import time
import psutil

# MQTT für OpenHAB
client = mqtt.Client()
client.connect("localhost", 1883, 60)

def get_system_metrics():
    """Echte System-Metriken sammeln"""
    # CPU Auslastung (Durchschnitt über 1 Sekunde)
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # RAM Auslastung
    memory = psutil.virtual_memory()
    ram_percent = memory.percent
    
    # Disk Auslastung
    disk = psutil.disk_usage('/')
    disk_percent = disk.percent
    
    # Netzwerk-Traffic (Bytes pro Sekunde)
    net_io = psutil.net_io_counters()
    
    # Anzahl laufende Prozesse
    process_count = len(psutil.pids())
    
    return {
        'cpu_percent': round(cpu_percent, 1),
        'ram_percent': round(ram_percent, 1),
        'disk_percent': round(disk_percent, 1),
        'process_count': process_count,
        'network_bytes_sent': net_io.bytes_sent,
        'network_bytes_recv': net_io.bytes_recv
    }

def get_face_recognition_metrics():
    """Relevante Face Recognition Metriken für Entwicklungsphase"""
    try:
        import sqlite3
        import requests
        from datetime import datetime, timedelta
        
        # 1. STREAM SERVER STATUS - ist das System überhaupt aktiv?
        try:
            response = requests.get('http://localhost:5000/health', timeout=2)
            if response.status_code == 200:
                health_data = response.json()
                stream_server_online = True
                known_faces_count = health_data.get('known_faces', 0)
            else:
                stream_server_online = False
                known_faces_count = 0
        except:
            stream_server_online = False
            known_faces_count = 0
        
        # 2. FACE DETECTION ACTIVITY - aktuelle und durchschnittliche Confidence
        try:
            # Prüfe Live-Metriken über /metrics endpoint
            response = requests.get('http://localhost:5000/metrics', timeout=2)
            if response.status_code == 200:
                metrics_data = response.json()
                current_confidence = metrics_data.get('current_confidence', 0)
                average_confidence = metrics_data.get('average_confidence', 0)
                face_currently_recognized = metrics_data.get('face_recognized', False)
            else:
                current_confidence = 0
                average_confidence = 0
                face_currently_recognized = False
        except:
            current_confidence = 0
            average_confidence = 0
            face_currently_recognized = False
        
        # 3. SYSTEM PERFORMANCE für ML Processing
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        ram_percent = memory.percent
        
        # Face Recognition Performance Score (CPU + RAM Einfluss)
        performance_score = max(0, 100 - (cpu_percent * 0.5) - (ram_percent * 0.3))
        
        return {
            'stream_server_online': int(stream_server_online),  # 1 = online, 0 = offline
            'known_faces_count': known_faces_count,
            'current_confidence': round(current_confidence, 1),
            'average_confidence': round(average_confidence, 1),
            'face_currently_detected': int(face_currently_recognized),
            'ml_performance_score': round(performance_score, 1)
        }
        
    except Exception as e:
        print(f"⚠️ Face Recognition Metriken nicht verfügbar: {e}")
        return {
            'stream_server_online': 0,
            'known_faces_count': 0,
            'current_confidence': 0.0,
            'average_confidence': 0.0,
            'face_currently_detected': 0,
            'ml_performance_score': 0.0
        }

def send_system_data():
    """System- und Face Recognition Daten senden"""
    try:
        # System Metriken
        system_metrics = get_system_metrics()
        
        # Face Recognition Metriken
        face_metrics = get_face_recognition_metrics()
        
        # System Daten an OpenHAB
        client.publish("system/cpu_percent", system_metrics['cpu_percent'])
        client.publish("system/ram_percent", system_metrics['ram_percent'])
        client.publish("system/disk_percent", system_metrics['disk_percent'])
        
        # Face Recognition Daten an OpenHAB - ALLE METRIKEN
        client.publish("facerecog/server_online", face_metrics['stream_server_online'])
        client.publish("facerecog/known_faces", face_metrics['known_faces_count'])
        client.publish("facerecog/current_confidence", face_metrics['current_confidence'])
        client.publish("facerecog/average_confidence", face_metrics['average_confidence'])
        client.publish("facerecog/face_detected", face_metrics['face_currently_detected'])
        client.publish("facerecog/ml_performance", face_metrics['ml_performance_score'])
        
        # Status ausgeben
        server_status = "🟢 ONLINE" if face_metrics['stream_server_online'] else "🔴 OFFLINE"
        face_status = "👤 ERKANNT" if face_metrics['face_currently_detected'] else "🔍 SUCHE"
        
        print(f"✅ CPU: {system_metrics['cpu_percent']}% | RAM: {system_metrics['ram_percent']}% | Server: {server_status} | {face_status}")
        print(f"   🎯 Live: {face_metrics['current_confidence']}% | Ø: {face_metrics['average_confidence']}% | Faces: {face_metrics['known_faces_count']} | ML: {face_metrics['ml_performance_score']}%")
        
    except Exception as e:
        print(f"❌ Fehler beim Sammeln der Metriken: {e}")

if __name__ == "__main__":
    print("🚀 Face Recognition System Monitoring gestartet...")
    print("📊 Sammle echte CPU/RAM/Disk Daten...")
    
    try:
        while True:
            send_system_data()
            time.sleep(10)  # Alle 30 Sekunden
    except KeyboardInterrupt:
        print("\n🛑 Monitoring beendet")
        client.disconnect()