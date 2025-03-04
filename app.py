import cv2
import face_recognition
import pymongo
import os
import numpy as np
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, request, send_from_directory
import firebase_admin
from firebase_admin import credentials, storage
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import shutil  # Add this import for folder deletion

# Define Indian Standard Time (IST)
IST = timezone(timedelta(hours=5, minutes=30))

# Get current UTC time and convert to IST
current_time_utc = datetime.now(timezone.utc)
current_time_ist = current_time_utc.astimezone(IST)

# Print debugging information
print("UTC Time:", current_time_utc.strftime('%Y-%m-%d %H:%M:%S'))
print("IST Time:", current_time_ist.strftime('%Y-%m-%d %H:%M:%S'))

# Extract hour and minute in IST
ist_hour = current_time_ist.hour
ist_minute = current_time_ist.minute

# Check if current time falls in the lunch period (13:30 - 14:30 IST)
is_lunch_time = (ist_hour == 13 and ist_minute >= 30) or (ist_hour == 14 and ist_minute < 30)

print("Is Lunch Time:", is_lunch_time)  # Debugging

# Initialize Firebase Admin SDK
cred = credentials.Certificate("serviceAccountKey.json")  # Path to your Firebase service account key
firebase_admin.initialize_app(cred, {
    'storageBucket': 'new-project-zmwdpy.appspot.com'  # Replace with your Firebase Storage bucket name
})

app = Flask(__name__)

# Connect to MongoDB
db_client = pymongo.MongoClient("mongodb://localhost:27017/")  # Replace with your MongoDB connection string
db = db_client["vid_attendance"]  # Database name
users_collection = db["officeusers"]  # Collection for users
attendance_collection = db["attendancedata"]  # Collection for attendance

# Create a folder to store captured images
if not os.path.exists("captured_images"):
    os.makedirs("captured_images")

# Function to retrieve user encodings from MongoDB
def get_user_encodings():
    users = users_collection.find()
    user_encodings = []
    
    for user in users:
        user_id = str(user["_id"])
        name = user["username"]
        image_path = user["image_path"]
        if os.path.exists(image_path):
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            if encodings:
                user_encodings.append((user_id, name, encodings[0], image_path))
    
    return user_encodings

user_encodings = get_user_encodings()

# Initialize webcam
video_capture = cv2.VideoCapture(0)

# Global variable to store the captured image
captured_image_path = None

# Face recognition function
def generate_frames():
    while True:
        success, frame = video_capture.read()
        if not success:
            break

        _, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/captured_images/<filename>')
def serve_captured_image(filename):
    return send_from_directory('captured_images', filename)

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    global captured_image_path
    success, frame = video_capture.read()
    if not success:
        return jsonify({'error': 'Failed to capture photo'}), 500

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    captured_image_path = f"captured_images/captured_{timestamp}.jpg"
    cv2.imwrite(captured_image_path, frame)

    captured_image = face_recognition.load_image_file(captured_image_path)
    captured_encoding = face_recognition.face_encodings(captured_image)

    if not captured_encoding:
        return jsonify({'error': 'No face detected'}), 400

    captured_encoding = captured_encoding[0]
    face_distances = face_recognition.face_distance([encoding for _, _, encoding, _ in user_encodings], captured_encoding)
    confidence_threshold = 0.5
    best_match_index = np.argmin(face_distances)
    best_match_distance = face_distances[best_match_index]

    if best_match_distance <= confidence_threshold:
        matched_user_id, matched_name, _, matched_image_path = user_encodings[best_match_index]
        
        return jsonify({
            'matched': True,
            'user_id': matched_user_id,
            'name': matched_name,
            'image': captured_image_path,
            'confidence': 1 - best_match_distance
        })
    
    return jsonify({'matched': False, 'confidence': 1 - best_match_distance})

# Function to upload image to Firebase Storage
def upload_to_firebase(image_path):
    bucket = storage.bucket()
    blob = bucket.blob(f"attendance_images/{os.path.basename(image_path)}")
    blob.upload_from_filename(image_path)
    blob.make_public()
    return blob.public_url

@app.route('/add_attendance', methods=['POST'])
def add_attendance():
    global captured_image_path
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({'success': False, 'error': 'User ID is required'}), 400

    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        user_name = user['username']
        officialId = user['officialId']
        email = user.get('email', '')
        current_time = datetime.utcnow()
        today = current_time.strftime('%Y-%m-%d')
        firebase_image_url = upload_to_firebase(captured_image_path)

        # Find existing attendance record for the day
        existing_attendance = attendance_collection.find_one({
            "VID": officialId,
            "date": {"$gte": datetime.strptime(today, '%Y-%m-%d')}
        })

        if existing_attendance:
            update_data = {"updatedAt": current_time}

            breakIn_list = existing_attendance.get("breakIn", [])
            breakOut_list = existing_attendance.get("breakOut", [])
            is_before_7pm = current_time.hour < 19

            if existing_attendance.get("In") and not is_lunch_time and len(breakIn_list) == len(breakOut_list) and is_before_7pm:
                # Add breakIn to the array
                update_data["breakIn"] = existing_attendance.get("breakIn", []) + [{"time": current_time, "image": firebase_image_url}]
            elif existing_attendance.get("In") and not is_lunch_time and is_before_7pm:
                # Add breakOut to the array
                update_data["breakOut"] = existing_attendance.get("breakOut", []) + [{"time": current_time, "image": firebase_image_url}]
            else:
                # Handle In, Out, lunchIn, and lunchOut as before
                if is_lunch_time:
                    if not existing_attendance.get("lunchIn") or not existing_attendance["lunchIn"].get("time"):
                        update_data["lunchIn"] = {"time": current_time, "image": firebase_image_url}
                    elif not existing_attendance.get("lunchOut") or not existing_attendance["lunchOut"].get("time"):
                        update_data["lunchOut"] = {"time": current_time, "image": firebase_image_url}
                    else:
                        return jsonify({'success': False, 'error': 'Lunch time already recorded for today'}), 400
                else:
                    if not existing_attendance.get("Out") or not existing_attendance["Out"].get("time"):
                        update_data["Out"] = {"time": current_time, "image": firebase_image_url}
                    else:
                        return jsonify({'success': False, 'error': 'Out time already recorded for today'}), 400

            # Update the existing attendance record
            attendance_collection.update_one(
                {"_id": existing_attendance['_id']},
                {"$set": update_data}
            )
        else:
            # Create a new attendance record if none exists for the day
            attendance_record = {
                "VID": officialId,
                "name": user_name,
                "date": current_time,
                "In": {"time": current_time, "image": firebase_image_url},
                "Out": {"time": None, "image": ""},
                "lunchIn": {"time": None, "image": ""},
                "lunchOut": {"time": None, "image": ""},
                "breakIn": [],
                "breakOut": [],
                "email": email,
                "createdAt": current_time,
                "updatedAt": current_time
            }
            attendance_collection.insert_one(attendance_record)

        # Delete the captured_images folder after the record is saved
        if os.path.exists("captured_images"):
            shutil.rmtree("captured_images")
            print("Deleted captured_images folder.")
        if not os.path.exists("captured_images"):
            os.makedirs("captured_images")
        captured_image_path = None
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
        
@app.route('/get_attendance', methods=['GET'])
def get_attendance():
    try:
        attendance_data = list(attendance_collection.find().sort([
            ("updatedAt", -1),  # Sort by updatedAt in descending order
            ("createdAt", -1)   # If updatedAt is null, sort by createdAt
        ]))

        for record in attendance_data:
            record['_id'] = str(record['_id'])
            
        return jsonify({'success': True, 'attendance_data': attendance_data}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)