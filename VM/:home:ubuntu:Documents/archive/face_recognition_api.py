from flask import Flask, request, jsonify
import face_recognition
import numpy as np
import cv2
from PIL import Image
import io
import os

app = Flask(__name__)

# Store known face encodings (you'll need to add these)
known_face_encodings = []
known_face_names = []

def load_known_faces():
    """Load known faces from images directory"""
    global known_face_encodings, known_face_names
    
    # Create known_faces directory if it doesn't exist
    os.makedirs('known_faces', exist_ok=True)
    
    # Load any existing face images
    for filename in os.listdir('known_faces'):
        if filename.endswith(('.jpg', '.jpeg', '.png')):
            image_path = os.path.join('known_faces', filename)
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            
            if encodings:
                known_face_encodings.append(encodings[0])
                # Use filename without extension as name
                name = os.path.splitext(filename)[0]
                known_face_names.append(name)
                print(f"Loaded face: {name}")

@app.route('/recognize', methods=['POST'])
def recognize_face():
    try:
        # Get image from request
        if 'image' not in request.files:
            return jsonify({'error': 'No image provided'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'error': 'No image selected'}), 400
        
        # Read image
        image_bytes = file.read()
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        # Convert BGR to RGB
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # Find faces and encodings
        face_locations = face_recognition.face_locations(rgb_image)
        face_encodings = face_recognition.face_encodings(rgb_image, face_locations)
        
        if not face_encodings:
            return jsonify({
                'face_recognized': False,
                'user_name': None,
                'message': 'No faces found in image'
            })
        
        # Check against known faces
        for face_encoding in face_encodings:
            if known_face_encodings:
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                
                best_match_index = np.argmin(face_distances)
                
                if matches[best_match_index] and face_distances[best_match_index] < 0.6:
                    name = known_face_names[best_match_index]
                    return jsonify({
                        'face_recognized': True,
                        'user_name': name,
                        'confidence': float(1 - face_distances[best_match_index]),
                        'message': f'Face recognized as {name}'
                    })
        
        return jsonify({
            'face_recognized': False,
            'user_name': None,
            'message': 'Face detected but not recognized'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'known_faces': len(known_face_encodings),
        'faces_loaded': known_face_names
    })

@app.route('/add_face', methods=['POST'])
def add_face():
    """Add a new face to the known faces"""
    try:
        if 'image' not in request.files or 'name' not in request.form:
            return jsonify({'error': 'Image and name required'}), 400
        
        file = request.files['image']
        name = request.form['name']
        
        # Save image
        image_path = f'known_faces/{name}.jpg'
        file.save(image_path)
        
        # Reload known faces
        load_known_faces()
        
        return jsonify({
            'message': f'Face for {name} added successfully',
            'total_faces': len(known_face_encodings)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Loading known faces...")
    load_known_faces()
    print(f"Loaded {len(known_face_encodings)} known faces")
    print("Starting Face Recognition API Server...")
    app.run(host='0.0.0.0', port=5000, debug=True)
