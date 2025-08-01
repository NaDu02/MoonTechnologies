# Face Recognition System

## Vorbereitung

Aktiviere die virtuelle Umgebung:
source ~/face_payment_env/bin/activate

## API starten auf OpenStack VM (muss immer laufen):
python face_recognition_api.py

## Skripte

### Live-System testen (WIP):
python face_recog_stream.py

### Neue Gesichter hinzufügen:
python headless_capture.py

### Face_Recog mit 3 Sekunden Delay starten (funktioniert):
python video_stream_main_full.py
