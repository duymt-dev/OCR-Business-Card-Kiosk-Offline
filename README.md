# Registration Kiosk (Offline-First)

**Python 3.11.9 · Flask · HTML/JS/CSS · Nimiq QR Scanner · OCR (RapidOCR) · YOLOv8**

A dual-camera kiosk application designed for event check-ins and registrations. It supports scanning QR codes, business cards, and capturing face images. The system operates offline, stores data locally, and can optionally forward registrations to an external API.

---

## ✨ Key Features

- **Dual Camera Support**:
  - **Camera 1 (Top-down)**: Scans QR codes (Nimiq), ID cards, and business cards. Supports autonomous OCR processing.
  - **Camera 2 (Front-facing)**: Captures face images and supports human presence detection (YOLO).
- **Advanced OCR Pipeline**:
  - Business card detection using **YOLOv8**.
  - High-accuracy text extraction via **RapidOCR**.
  - **Language Support**: Primarily optimized for **Japanese** cards, **Mixed** (JP/EN) cards, and **English** cards.
  - Intelligent data fields parsing (Name, Title, Company, Email, Phone, Address).
  - Optional **LLM Integration** (Qwen2.5) for superior structured data extraction.
- **Offline-First Storage**: Saves all registrations locally in `registrations/<REG_ID>/` with JSON metadata, scanned images, and face photos.
- **Built-in Dashboard**: Manage, view, and export registrations to Excel.
- **External API Forwarding**: Optional background synchronization with external systems via Bearer token authentication.
- **User Feedback**: Interactive UI with audio guidance and registration QR printing.

---

## 🧱 Project Architecture

- **Frontend**: Vanilla HTML/CSS/JavaScript. Uses `qr-scanner.legacy.min.js` for client-side QR scanning.
- **Backend**: **Flask** (Python). Orchestrates camera streams, AI models, and database operations.
- **Database**: **SQLite** (via `database/`) for tracking registration status and metadata.
- **AI Models**:
  - **YOLOv8** (ONNX): Card detection and presence.
  - **SCRFD** (ONNX): High-speed face detection.
  - **Paddle Mobile OCR** (ONNX): Parameterized for low-resource environments (Raspberry Pi 5).

---

## 🚀 Quick Start

### 1. Prerequisites

- **OS**: Windows 10/11 or Linux (Tested on Raspberry Pi 5).
- **Python**: 3.11.9+ (recommended).
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

### 3. Run the Application

```bash
python application.py
```

Open your browser at `http://localhost:5000`.

---

## 📦 Deployment & Build (EXE)

To bundle the application into a standalone Windows executable:

```bash
pip install pyinstaller
pyinstaller -y saomaisoft.spec
```

After building, ensure the `models/` and  `.env` are copied to the `dist/saomaisoft` folder.

---

## 🛠️ Calibration & Customization

- **Camera Calibration**: Use `camera_calibration.py` to generate `camera_calibration.json` for fisheye undistortion.
- **QR Region**: Adjust the scanning area in `static/js/mainpy.js` (`_calculateScanRegion`).
- **UI Flow**: Detailed analysis and optimization plans are available in the `mds/` directory.

---
# OCR-Business-Card-Kiosk-Offline
Developed an offline-first Kiosk application on Raspberry Pi 5 for events. It uses two cameras to scan documents (QR codes, ID cards, business cards), detect faces, give voice feedback, and print registration QR codes.
