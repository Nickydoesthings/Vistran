import sys
import io
import os
import base64
import requests
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
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

class TranslationDisplayWindow(QtWidgets.QWidget):
    def __init__(self, initial_text, rect):
        super().__init__()

        # Remove window decorations and make the window stay on top
        self.setWindowFlags(
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.FramelessWindowHint
        )

        # Set window geometry to match the selected rectangle
        self.setGeometry(rect)

        # Set background color, border, and rounded corners
        self.setStyleSheet("""
            background-color: rgba(50, 50, 50, 220);
            border: 2px solid #333333;
            border-radius: 10px;
        """)

        # Add drop shadow effect
        self.shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(15)
        self.shadow.setXOffset(0)
        self.shadow.setYOffset(0)
        self.shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(self.shadow)

        # Create a layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Create a close button with "X"
        close_button = QtWidgets.QPushButton("X")
        close_button.setFixedSize(24, 24)
        close_button.setStyleSheet("""
            QPushButton {
                background-color: black;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: red;
            }
        """)
        close_button.clicked.connect(self.close)

        # Create a horizontal layout for the close button
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addStretch()
        top_layout.addWidget(close_button)

        # Create a text label to display the translation
        self.text_label = QtWidgets.QLabel(initial_text)
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(QtCore.Qt.AlignCenter)
        self.text_label.setStyleSheet("color: white;")
        self.text_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # Set initial font
        self.font = QtGui.QFont("Arial", 14)
        self.text_label.setFont(self.font)

        # Add close button and text label to the main layout
        layout.addLayout(top_layout)
        layout.addWidget(self.text_label)

        self.setLayout(layout)

    def resizeEvent(self, event):
        # Adjust font size based on the window height
        new_height = self.height()
        # Simple scaling: font size is a fraction of the window height
        font_size = max(10, int(new_height * 0.05))
        self.font.setPointSize(font_size)
        self.text_label.setFont(self.font)
        super().resizeEvent(event)

    @QtCore.pyqtSlot(str)
    def update_text(self, new_text):
        """Update the text displayed in the window."""
        self.text_label.setText(new_text)


class TranslationTask(QtCore.QRunnable):
    def __init__(self, img_bytes, translation_window, app_instance):
        super().__init__()
        self.img_bytes = img_bytes
        self.translation_window = translation_window
        self.app_instance = app_instance

    def run(self):
        # Perform the translation in the background
        translated_text = self.app_instance.call_openai_api(self.img_bytes)
        if translated_text:
            logging.info("Translation successful.")
            # Emit the signal with the translated text
            self.app_instance.translation_ready.emit(translated_text)
        else:
            logging.error("Translation failed.")
            QtCore.QMetaObject.invokeMethod(
                self.app_instance,
                "show_error",
                QtCore.Qt.QueuedConnection
            )


class TranslatorApp(QtWidgets.QWidget):
    translation_ready = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.translation_windows = []
        self.init_ui()
        self.translation_ready.connect(self.update_translation)

    def init_ui(self):
        logging.info("Initializing UI.")
        self.setWindowTitle('Vistran: Visual Translator')
        self.setGeometry(100, 100, 300, 200)  # Adjusted size for the simplified layout

        # Main Layout
        main_layout = QtWidgets.QVBoxLayout()

        # Capture Button
        self.capture_button = QtWidgets.QPushButton('Capture Screenshot', self)
        self.capture_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-size: 16px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.capture_button.clicked.connect(self.capture_screenshot)
        main_layout.addWidget(self.capture_button)

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
            self.selected_rect = rect
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

            # Show the translation window with "Translating..." text immediately
            self.translation_window = TranslationDisplayWindow("Translating...", self.selected_rect)
            self.translation_window.show()
            self.translation_windows.append(self.translation_window)

            # Now process the image and update the window with the actual translation
            self.process_image(img)
        except Exception as e:
            logging.exception("Failed during screenshot processing.")
            self.show()

    def process_image(self, pil_image):
        logging.info("Processing captured image.")
        # Convert PIL Image to bytes
        img_byte_arr = io.BytesIO()
        pil_image.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        logging.info("Image successfully converted to bytes.")

        # Create and start the translation task in a separate thread
        translation_task = TranslationTask(img_bytes, self.translation_window, self)
        QtCore.QThreadPool.globalInstance().start(translation_task)

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
                    {"role": "system", "content": "You are a helpful translation assistant."},
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

    @QtCore.pyqtSlot()
    def show_error(self):
        QtWidgets.QMessageBox.critical(self, "Error", "Failed to get translation.")

    @QtCore.pyqtSlot(str)
    def update_translation(self, translated_text):
        if self.translation_window:
            self.translation_window.update_text(translated_text)

def main():
    logging.info("Starting Visual Translator application.")
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QIcon('images/v-letter.svg'))
    translator = TranslatorApp()
    translator.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
