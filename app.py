import re
from flask import Flask, request, render_template, send_file, jsonify
import os
from werkzeug.utils import secure_filename
import subprocess
import threading
import sqlite3
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = './uploads'
CONVERTED_FOLDER = './converted'
DATABASE = '/app/conversion_history.db'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CONVERTED_FOLDER'] = CONVERTED_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)

conversion_status = {}

# Initialize the database
def init_db():
    try:
        if not os.path.exists(DATABASE):
            logging.info(f"Database {DATABASE} does not exist. Creating it now.")
        with sqlite3.connect(DATABASE) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_file TEXT NOT NULL,
                    converted_file TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            logging.info("Ensured 'history' table exists.")
            conn.commit()
    except Exception as e:
        logging.error(f"Error during database initialization: {e}")

init_db()

# Save a record to the database
def save_to_db(original_file, converted_file, status):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            'INSERT INTO history (original_file, converted_file, status) VALUES (?, ?, ?)',
            (original_file, converted_file, status)
        )
        conn.commit()
        return cursor.lastrowid

# Update the status of a record in the database
def update_status_in_db(task_id, status):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('UPDATE history SET status = ? WHERE id = ?', (status, task_id))
        conn.commit()

# Get all records from the database
def fetch_all_history():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute('SELECT * FROM history')
        return cursor.fetchall()

# Parse ffmpeg output for progress
def parse_ffmpeg_output(line, duration):
    time_match = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
    speed_match = re.search(r"speed=([\d\.]+)x", line)
    time_in_seconds = 0

    if time_match:
        time_parts = list(map(float, time_match.group(1).split(':')))
        time_in_seconds = time_parts[0] * 3600 + time_parts[1] * 60 + time_parts[2]
    if duration > 0:
        progress = min((time_in_seconds / duration) * 100, 100)
    else:
        progress = 0

    return {
        "progress": round(progress, 2),
        "speed": speed_match.group(1) if speed_match else "N/A",
        "elapsed": time_in_seconds
    }

# Conversion function
def convert_video_to_mp3(input_path, output_path, task_id):
    try:
        # Get video duration
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        duration = float(result.stdout.decode().strip())
        
        process = subprocess.Popen(
            ['ffmpeg', '-i', input_path, output_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        for line in process.stdout:
            if "time=" in line:
                progress_info = parse_ffmpeg_output(line, duration)
                conversion_status[task_id] = {
                    "status": "in_progress",
                    "progress": progress_info["progress"],
                    "speed": progress_info["speed"],
                    "elapsed": progress_info["elapsed"]
                }
        process.wait()
        if process.returncode == 0:
            conversion_status[task_id] = {
                "status": "completed",
                "progress": 100,
                "speed": "N/A",
                "elapsed": duration
            }
            update_status_in_db(task_id, "completed")
        else:
            conversion_status[task_id] = {
                "status": "failed",
                "progress": 0,
                "speed": "N/A",
                "elapsed": 0
            }
            update_status_in_db(task_id, "failed")
    except Exception as e:
        conversion_status[task_id] = {
            "status": "error",
            "progress": 0,
            "speed": "N/A",
            "elapsed": 0,
            "error": str(e)
        }
        update_status_in_db(task_id, "error")

@app.route('/')
def index():
    history = fetch_all_history()
    formatted_history = [
        {
            "id": row[0],
            "original_file": row[1],
            "converted_file": row[2],
            "status": row[3]
        }
        for row in history
    ]
    return render_template('index.html', history=formatted_history)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        filename = secure_filename(file.filename)
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(input_path)

        base_filename = os.path.splitext(filename)[0]
        output_path = os.path.join(app.config['CONVERTED_FOLDER'], f"{base_filename}.mp3")

        task_id = save_to_db(filename, os.path.basename(output_path), "in_progress")

        conversion_status[task_id] = {"status": "in_progress", "progress": 0}
        thread = threading.Thread(target=convert_video_to_mp3, args=(input_path, output_path, task_id))
        thread.start()

        return jsonify({'message': 'File is being converted', 'task_id': task_id})

@app.route('/status/<int:task_id>', methods=['GET'])
def get_status(task_id):
    return jsonify(conversion_status.get(task_id, {"status": "unknown"}))

@app.route('/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT converted_file FROM history WHERE id = ?", (file_id,))
            file = cursor.fetchone()
            if not file:
                return jsonify({"error": "File not found"}), 404
            
            file_path = os.path.join(CONVERTED_FOLDER, file[0])
            if os.path.exists(file_path):
                os.remove(file_path)
            cursor.execute("DELETE FROM history WHERE id = ?", (file_id,))
            conn.commit()
            return jsonify({"message": "File deleted successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    file_path = os.path.join(CONVERTED_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5577)
