import cv2
import time
import os
import requests

def capture_and_upload():
    """Capture image, ask for name, and upload to VM automatically"""
    # Create directory
    os.makedirs('captured_images', exist_ok=True)
    
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("❌ Camera not accessible")
        return False
    
    print("📷 Capturing image in 3 seconds...")
    time.sleep(1)
    print("3...")
    time.sleep(1)
    print("2...")
    time.sleep(1)
    print("1...")
    
    # Capture frame
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        print("❌ Failed to capture image")
        return False
    
    # Save image
    timestamp = int(time.time())
    filename = f'captured_images/capture_{timestamp}.jpg'
    cv2.imwrite(filename, frame)
    print(f"✅ Image captured: {filename}")
    
    # Ask for name
    while True:
        name = input("👤 Enter person's name: ").strip()
        if name:
            break
        print("❌ Name cannot be empty. Please try again.")
    
    # Upload to VM
    print(f"🌐 Uploading {name}'s face to recognition server...")
    vm_url = "http://141.72.12.186:5000/add_face"
    
    try:
        with open(filename, 'rb') as img_file:
            files = {'image': img_file}
            data = {'name': name}
            response = requests.post(vm_url, files=files, data=data)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ SUCCESS: {name} added to face recognition system!")
            print(f"📊 Server response: {result}")
        else:
            print(f"❌ FAILED: Server returned status {response.status_code}")
            print(f"📝 Response: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error: Could not reach VM server")
        print(f"🔍 Error details: {e}")
        print(f"💡 Make sure VM server is running on http://141.72.12.186:5000")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False
    
    return True

def check_server_status():
    """Check if the VM server is running"""
    try:
        response = requests.get("http://141.72.12.186:5000/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"🟢 Server is running!")
            print(f"📊 Known faces: {data.get('known_faces', 0)}")
            if 'faces_loaded' in data:
                print(f"👥 Registered people: {data['faces_loaded']}")
            return True
        else:
            print(f"🟡 Server responding but status: {response.status_code}")
            return False
    except:
        print(f"🔴 Server not reachable at http://141.72.12.186:5000")
        print(f"💡 Make sure the VM face recognition server is running")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("🎯 Face Recognition - Capture & Upload System")
    print("=" * 50)
    
    # Check server first
    print("\n1️⃣ Checking VM server status...")
    if not check_server_status():
        print("\n❌ Cannot proceed without server connection")
        exit(1)
    
    print("\n2️⃣ Starting capture process...")
    capture_and_upload()
    
    print("\n🎉 Process completed!")
