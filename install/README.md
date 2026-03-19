# Registration Kiosk (Offline-First)
 
**Python 3.12.4 · Flask · HTML/JS/CSS · Nimiq QR Scanner · OCR (EasyOCR/RapidOCR) · YOLOv8**
 
A dual-camera kiosk application designed for event check-ins and registrations. It supports scanning QR codes, ID cards (CCCD/MRZ), business cards, and capturing face images. The system operates offline, stores data locally, and can optionally forward registrations to an external API.
 
---
 
## ✨ Key Features
 
- **Dual Camera Support**:
    - **Camera 1 (Top-down)**: Scans QR codes (Nimiq), ID cards, and business cards. Supports autonomous OCR processing.
    - **Camera 2 (Front-facing)**: Captures face images and supports human presence detection (YOLO).
- **Advanced OCR Pipeline**:
    - Business card detection using **YOLOv8**.
    - High-accuracy text extraction via **EasyOCR** or **RapidOCR**.
    - Intelligent data fields parsing (Name, Title, Company, Email, Phone, Address).
    - Optional **LLM Integration** (Qwen2.5) for superior structured data extraction.
- **Offline-First Storage**: Saves all registrations locally in `registrations/<REG_ID>/` with JSON metadata, scanned images, and face photos.
- **Built-in Dashboard**: Manage, view, and export registrations to Excel.
- **External API Forwarding**: Optional background synchronization with external systems via Bearer token authentication.
- **User Feedback**: Interactive UI with audio guidance and registration QR printing.
 
---
 
## 🧱 Project Architecture
 
- **Frontend**: Vanilla HTML/CSS/JavaScript. Uses `qr-scanner.legacy.min.js` for client-side QR scanning and `Tesseract.js` for optional client-side OCR.
- **Backend**: **Flask** (Python). Orchestrates camera streams, AI models, and database operations.
- **Database**: **SQLite** (via `database/`) for tracking registration status and metadata.
- **AI Models**:
    - **YOLOv8** (ONNX): Card detection and presence.
    - **SCRFD** (ONNX): High-speed face detection.
    - **Mobile OCR** (ONNX): Parameterized for low-resource environments (Raspberry Pi 5).
 
---
 
## 🚀 Quick Start
 
### 1. Prerequisites
- **OS**: Windows 10/11 or Linux (Tested on Raspberry Pi 5).
- **Python**: 3.12.4+ (recommended).
- **Browser**: Chrome or Edge (mandatory for WebRTC camera access).
 
### 2. Installation
 
```bash
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate  # Windows
source .venv/bin/activate # Linux
 
# Install dependencies
python -m pip install -U pip
pip install -r requirements.txt
```
 
*Note: For LLM support, install `llama-cpp-python` with the appropriate backend (e.g., Vulkan or CPU).*
 
### 3. Configuration
Create a `.env` file in the root directory:
 
```env
HOST=127.0.0.1
PORT=5000
 
# Forward API (Optional)
FORWARD_ENABLED=false
FORWARD_URL=https://api.example.com/register
FORWARD_API_KEY=your_token_here
 
# AI Models
YOLO_MODEL=models/card/best_int8.onnx
FACE_MODEL=models/scrfd_500m_bnkps.onnx
ENABLE_LLM=true
LLM_GGUF=models/qwen2.5-1.5b-instruct-q4_k_m.gguf
```
 
### 4. Run the Application
```bash
python app.py
```
Open your browser at `http://localhost:5000`.
 
---
 
## 📦 Deployment & Build (EXE)
 
To bundle the application into a standalone Windows executable:
 
```bash
pip install pyinstaller
pyinstaller -y saomaisoft.spec
```
 
After building, ensure the `models/`, `.env`, and `easyocr_models/` directories are copied to the `dist/saomaisoft` folder.
 
---
 
## 🛠️ Calibration & Customization
 
- **Camera Calibration**: Use `camera_calibration.py` to generate `camera_calibration.json` for fisheye undistortion.
- **QR Region**: Adjust the scanning area in `static/js/mainpy.js` (`_calculateScanRegion`).
- **UI Flow**: Detailed analysis and optimization plans are available in the `mds/` directory.
 
---
 
## 📄 License
Copyright © 2026. Built with focus on professional event registration stability.