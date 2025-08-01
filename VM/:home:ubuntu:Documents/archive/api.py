from flask import Flask, request, jsonify
import face_recognition
import numpy as np
import os

app = Flask(__name__)

known_face_encodings = []
known_face_names = []

def load_known_faces():
    """Lädt alle Gesichter aus dem Ordner 'known_faces'"""
    global known_face_encodings, known_face_names
    os.makedirs('known_faces', exist_ok=True)

    for filename in os.listdir('known_faces'):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            image_path = os.path.join('known_faces', filename)
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            if encodings:
                known_face_encodings.append(encodings[0])
                name = os.path.splitext(filename)[0]
                known_face_names.append(name)
                print(f"Loaded face: {name}")

@app.route('/recognize', methods=['POST'])
def recognize_face():
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400

    image_file = request.files['image']

    try:
        image = face_recognition.load_image_file(image_file)
        face_locations = face_recognition.face_locations(image)
        face_encodings = face_recognition.face_encodings(image, face_locations)

        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
            face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)

            if True in matches:
                best_match_index = np.argmin(face_distances)
                name = known_face_names[best_match_index]
                confidence = 1 - face_distances[best_match_index]  # 1.0 = sicher

                return jsonify({
                    'face_recognized': True,
                    'user_name': name,
                    'confidence': float(confidence)
                })

        return jsonify({'face_recognized': False})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Lade bekannte Gesichter...")
    load_known_faces()
    print("Starte Flask Server auf Port 5000")
    app.run(host='0.0.0.0', port=5000)
