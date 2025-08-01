#!/usr/bin/env python3
import os
import sys
import subprocess
import logging
import time

# Logging mit Datei
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/Documents/debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    try:
        logger.info("=== DEBUG: Starting product recognition ===")
        
        # Pfade prüfen
        script_dir = '/home/ubuntu/Documents/product_recog'
        venv_python = '/home/ubuntu/Documents/face_recognition_env/bin/python3'
        target_script = '/home/ubuntu/Documents/product_recog/product_recog.py'
        
        logger.info(f"Working directory: {script_dir}")
        logger.info(f"Python path: {venv_python}")
        logger.info(f"Target script: {target_script}")
        
        # Verzeichnis wechseln
        os.chdir(script_dir)
        logger.info(f"Changed to: {os.getcwd()}")
        
        # Dateien prüfen
        if not os.path.exists(venv_python):
            logger.error(f"Python not found: {venv_python}")
            return 1
            
        if not os.path.exists(target_script):
            logger.error(f"Script not found: {target_script}")
            return 1
            
        # Prüfen ob bereits läuft
        check_cmd = ["pgrep", "-f", "product_recog.py"]
        result = subprocess.run(check_cmd, capture_output=True)
        if result.returncode == 0:
            logger.info("Product recognition already running")
            print("Product recognition already running")
            return 0
        
        # LÖSUNG 1: Als echter Hintergrundprozess starten
        cmd = [venv_python, target_script]
        logger.info(f"Executing: {' '.join(cmd)}")
        
        # Mit nohup und & für echten Hintergrundprozess
        full_cmd = f"nohup {venv_python} {target_script} > /home/ubuntu/Documents/product_recog.log 2>&1 &"
        
        process = subprocess.Popen(
            full_cmd,
            shell=True,
            preexec_fn=os.setsid  # Neue Prozessgruppe erstellen
        )
        
        logger.info(f"Background process started")
        
        # Kurz warten und prüfen
        time.sleep(3)
        
        # Prüfen ob Prozess läuft
        check_result = subprocess.run(check_cmd, capture_output=True)
        if check_result.returncode == 0:
            pid = check_result.stdout.decode().strip()
            logger.info(f"Product recognition started successfully (PID: {pid})")
            print(f"Product recognition started successfully (PID: {pid})")
            return 0
        else:
            logger.error("Process not found after start")
            print("Error: Process not found after start")
            return 1
        
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        print(f"Error: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
