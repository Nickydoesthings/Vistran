import sys
import io
import os
import base64
import requests
from PyQt5 import QtCore, QtGui, QtWidgets
from PIL import Image
import mss
import openai
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Make sure set set this up in env variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

API_URL = 'https://api.openai.com/v1/chat/completions'
MODEL_NAME = 'gpt-4o-mini'

class SelectionWindow(QtWidgets.QWidget):
    selection_made = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Select Region')
        self.setWindowOpacity(0.3)
        self.setWindowFlags(
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.Window
        )
        self.showFullScreen()

        self.origin = QtCore.QPoint()
        self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.origin = event.pos()
            self.rubberBand.setGeometry(QtCore.QRect(self.origin, QtCore.QSize()))
            self.rubberBand.show()

    def mouseMoveEvent(self, event):
        if self.rubberBand.isVisible():
            self.rubberBand.setGeometry(QtCore.QRect(self.origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.rubberBand.isVisible():
            self.rubberBand.hide()
            selected_rect = QtCore.QRect(self.origin, event.pos()).normalized()
            self.selection_made.emit(selected_rect)
            self.close()

    def closeEvent(self, event):
        logging.info("SelectionWindow is closing.")
        event.accept()

    def __del__(self):
        logging.info("SelectionWindow instance deleted.")

class TranslatorApp(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        logging.info("Initializing UI.")
        self.setWindowTitle('Visual Translator')
        self.setGeometry(100, 100, 800, 600)  # Increased size for better layout

        # Main Layout
        main_layout = QtWidgets.QHBoxLayout()

        # Left Layout (Button and Screenshot)
        left_layout = QtWidgets.QVBoxLayout()

        # Capture Button
        self.capture_button = QtWidgets.QPushButton('Capture Screenshot', self)
        self.capture_button.clicked.connect(self.capture_screenshot)
        left_layout.addWidget(self.capture_button)

        # Screenshot Display
        self.screenshot_label = QtWidgets.QLabel(self)
        self.screenshot_label.setFixedSize(400, 300)  # Adjust size as needed
        self.screenshot_label.setStyleSheet("border: 1px solid black;")
        self.screenshot_label.setAlignment(QtCore.Qt.AlignCenter)
        left_layout.addWidget(self.screenshot_label)

        # Right Layout (Translation)
        right_layout = QtWidgets.QVBoxLayout()

        # Text Area for Translation
        self.text_area = QtWidgets.QTextEdit(self)
        self.text_area.setReadOnly(True)
        right_layout.addWidget(self.text_area)

        # Add both layouts to the main layout
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)

        self.setLayout(main_layout)
        logging.info("UI initialized.")

    def capture_screenshot(self):
        try:
            logging.info("Starting screenshot capture.")
            self.hide()  # Hide the main window during selection

            self.selection_window = SelectionWindow()
            self.selection_window.selection_made.connect(self.on_selection_made)
            self.selection_window.show()
        except Exception as e:
            logging.exception("Failed to initiate screenshot capture.")
            self.show()

    def on_selection_made(self, rect):
        try:
            logging.info(f"User selected rectangle: {rect}")
            # Use mss to capture the selected region
            with mss.mss() as sct:
                monitor = {
                    "left": rect.left(),
                    "top": rect.top(),
                    "width": rect.width(),
                    "height": rect.height()
                }
                logging.info(f"Capturing screen: {monitor}")
                screenshot = sct.grab(monitor)

                # Convert the screenshot to a PIL Image with correct size and mode
                img = Image.frombytes("RGB", (screenshot.width, screenshot.height), screenshot.rgb)
                logging.info("Screenshot captured successfully.")

            # Show the main window before processing
            self.show()
            QtWidgets.QApplication.processEvents()  # Ensure UI updates

            # Explicitly delete the selection window
            if self.selection_window:
                self.selection_window.close()
                self.selection_window.deleteLater()
                self.selection_window = None

            self.process_image(img)
        except Exception as e:
            logging.exception("Failed during screenshot processing.")
            self.show()

    def process_image(self, pil_image):
        logging.info("Processing captured image.")
        self.text_area.setText("Processing...")

        # Display the screenshot in the UI
        qt_image = self.pil_to_qt(pil_image)
        self.screenshot_label.setPixmap(qt_image.scaled(
            self.screenshot_label.width(),
            self.screenshot_label.height(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        ))
        logging.info("Screenshot displayed in UI.")

        # Convert PIL Image to bytes
        img_byte_arr = io.BytesIO()
        pil_image.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        logging.info("Image successfully converted to bytes.")

        # Call OpenAI API
        translated_text = self.call_openai_api(img_bytes)
        if translated_text:
            logging.info("Translation successful.")
            self.text_area.setText(translated_text)
        else:
            logging.error("Translation failed.")
            self.text_area.setText("Failed to get translation.")

    def pil_to_qt(self, pil_image):
        """Convert PIL Image to QPixmap without using ImageQt."""
        # Ensure the image is in RGB mode
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        
        # Get image data as bytes
        data = pil_image.tobytes("raw", "RGB")
        
        # Create QImage from the data
        qimage = QtGui.QImage(data, pil_image.width, pil_image.height, QtGui.QImage.Format_RGB888)
        
        # Convert QImage to QPixmap
        pixmap = QtGui.QPixmap.fromImage(qimage)
        return pixmap

    def call_openai_api(self, image_bytes):
        logging.info("Calling OpenAI API.")
        try:
            # Encode image to base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            image_data_url = f"data:image/png;base64,{base64_image}"
            logging.info("Image successfully encoded to base64.")

            # Prepare the messages with image
            messages = [
                {
                    "type": "text",
                    "text": "Please extract any Japanese text from the image and translate it into English. Include only the translated text in your answer and do not give any context."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url
                    }
                }
            ]

            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": messages}
                ],
                "max_tokens": 300
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}"
            }

            logging.info("Sending request to OpenAI API.")
            response = requests.post(API_URL, headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                translated_text = result['choices'][0]['message']['content'].strip()
                logging.info("Received successful response from OpenAI API.")
                return translated_text
            else:
                logging.error(f"API Error: {response.status_code}, {response.text}")
                return None
        except Exception as e:
            logging.exception("Exception occurred during API call.")
            return None

def main():
    logging.info("Starting Visual Translator application.")
    app = QtWidgets.QApplication(sys.argv)
    translator = TranslatorApp()
    translator.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
