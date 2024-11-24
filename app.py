from flask import Flask, request, render_template, send_file, jsonify
import os
from werkzeug.utils import secure_filename
import subprocess

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = './uploads'
CONVERTED_FOLDER = './converted'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CONVERTED_FOLDER'] = CONVERTED_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)

conversion_history = []

@app.route('/')
def index():
    return render_template('index.html', history=conversion_history)

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

        # Convert to MP3
        base_filename = os.path.splitext(filename)[0]
        output_path = os.path.join(app.config['CONVERTED_FOLDER'], f"{base_filename}.mp3")
        try:
            subprocess.run(['ffmpeg', '-i', input_path, output_path], check=True)
        except subprocess.CalledProcessError as e:
            return jsonify({'error': f'Conversion failed: {str(e)}'}), 500

        # Add to history
        conversion_entry = {
            'original_file': filename,
            'converted_file': os.path.basename(output_path),
            'converted_path': output_path
        }
        conversion_history.append(conversion_entry)

        return jsonify({'message': 'File converted successfully', 'download_url': f"/download/{os.path.basename(output_path)}"})

@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5577)
