import sys
import io
import os
import base64
import requests
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication, QGraphicsView, QGraphicsScene, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsDropShadowEffect
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
from PyQt5.QtCore import Qt, QRectF
from PIL import Image
import mss
import openai
import logging
import pytesseract
from argostranslate import package, translate


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

        # Add instruction label
        self.instruction_label = QtWidgets.QLabel("Click and drag to select an area", self)
        self.instruction_label.setStyleSheet("""
            color: black; 
            background-color: rgba(255, 255, 255, 150);
            border-radius: 5px;
            padding: 5px;
        """)
        self.instruction_label.move(10, 10)

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

class TranslationDisplayWindow(QGraphicsView):
    def __init__(self, initial_text, rect):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setGeometry(rect)

        # Disable scroll bars
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Create scene and set it to the view
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        # Create a pixmap item for the background
        self.background = QGraphicsPixmapItem()
        self.scene.addItem(self.background)

        # Create blur effect
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(10)
        self.background.setGraphicsEffect(self.blur_effect)

        # Create semi-transparent overlay
        self.overlay = self.scene.addRect(QRectF(self.rect()), QPen(Qt.NoPen), QColor(255, 255, 255, 100))

        # Create text item
        self.text_item = self.scene.addText(initial_text)
        self.text_item.setDefaultTextColor(Qt.black)

        # Center the text
        self.center_text()

        # Add drop shadow effect to the text
        shadow_effect = QGraphicsDropShadowEffect()
        shadow_effect.setBlurRadius(5)
        shadow_effect.setOffset(2, 2)
        shadow_effect.setColor(QColor(0, 0, 0, 50))
        self.text_item.setGraphicsEffect(shadow_effect)

        # Create close button
        close_button = QtWidgets.QPushButton("X", self)
        close_button.setFixedSize(24, 24)
        close_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 100);
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 0, 0, 150);
            }
        """)
        close_button.clicked.connect(self.close)
        close_button.move(self.width() - 30, 5)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateBackground()
        self.overlay.setRect(QRectF(self.rect()))
        self.text_item.setTextWidth(self.width() - 20)

        # Re-center the text on resize
        self.center_text()

    def updateBackground(self):
        desktop = QApplication.desktop().screenNumber(QApplication.desktop().cursor().pos())
        screen = QApplication.screens()[desktop]
        pixmap = screen.grabWindow(0, self.x(), self.y(), self.width(), self.height())
        self.background.setPixmap(pixmap)

    def calculate_font_size(self, text):
        max_font_size = 72  # Increased maximum font size
        min_font_size = 10
        margin = 20
        available_height = self.height() - 2 * margin
        available_width = self.width() - 2 * margin

        # Binary search for the optimal font size
        low, high = min_font_size, max_font_size
        optimal_size = min_font_size

        while low <= high:
            mid = (low + high) // 2
            font = self.text_item.font()
            font.setPointSize(mid)
            self.text_item.setFont(font)
            self.text_item.setTextWidth(available_width)
            text_rect = self.text_item.boundingRect()

            if text_rect.width() <= available_width and text_rect.height() <= available_height:
                optimal_size = mid
                low = mid + 1  # Try a larger size
            else:
                high = mid - 1  # Try a smaller size

        return optimal_size

    def update_text(self, new_text):
        self.text_item.setPlainText(new_text)
        
        # Calculate and set the appropriate font size
        font_size = self.calculate_font_size(new_text)
        font = self.text_item.font()
        font.setPointSize(font_size)
        self.text_item.setFont(font)
        
        margin = 20
        self.text_item.setTextWidth(self.width() - 2 * margin)
        
        # Enable word wrap
        text_option = QtGui.QTextOption()
        text_option.setWrapMode(QtGui.QTextOption.WordWrap)
        self.text_item.document().setDefaultTextOption(text_option)
        
        # Re-center the text
        self.center_text()

    def center_text(self):
        text_rect = self.text_item.boundingRect()
        self.text_item.setPos((self.width() - text_rect.width()) / 2, (self.height() - text_rect.height()) / 2)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(200, 200, 200, 100))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

    def wheelEvent(self, event):
        # Override to disable scrolling with the mouse wheel
        event.ignore()

class TranslationTask(QtCore.QRunnable):
    def __init__(self, img_bytes, translation_window, app_instance):
        super().__init__()
        self.img_bytes = img_bytes
        self.translation_window = translation_window
        self.app_instance = app_instance

    def run(self):
        # Perform the translation in the background
        translated_text = self.app_instance.perform_translation(self.img_bytes)
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
        self.translation_type = "Online"  # Default to Online translation
        self.init_tesseract()
        self.init_ui()
        self.translation_ready.connect(self.update_translation)
        self.init_argos_translate()

    def init_tesseract(self):
        # Specify the path to the Tesseract executable
        tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'  # for Windows
        # tesseract_path = '/usr/local/bin/tesseract'  # Example for macOS/Linux
        
        if os.path.exists(tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            logging.info(f"Tesseract path set to: {tesseract_path}")
        else:
            logging.warning("Tesseract executable not found at the specified path. Make sure Tesseract is installed correctly.")

    def init_ui(self):
        logging.info("Initializing UI.")
        self.setWindowTitle('Vistran: Visual Translator')
        self.setGeometry(100, 100, 300, 200)  # Adjusted size for the simplified layout

        # Main Layout
        self.main_layout = QtWidgets.QVBoxLayout()

        # Create a stacked widget to hold different pages
        self.stacked_widget = QtWidgets.QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget)

        # Create main page
        self.main_page = QtWidgets.QWidget()
        main_page_layout = QtWidgets.QVBoxLayout(self.main_page)

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
        main_page_layout.addWidget(self.capture_button)

        # Options Button
        self.options_button = QtWidgets.QPushButton('Options', self)
        self.options_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-size: 16px;
                background-color: #D3D3D3;
                color: black;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #C0C0C0;
            }
        """)
        self.options_button.clicked.connect(self.show_options)
        main_page_layout.addWidget(self.options_button)

        # Create options page
        self.options_page = QtWidgets.QWidget()
        options_page_layout = QtWidgets.QVBoxLayout(self.options_page)

        # Translation Type Option
        translation_type_layout = QtWidgets.QHBoxLayout()
        translation_type_label = QtWidgets.QLabel("Translation Type:")
        self.translation_type_combo = QtWidgets.QComboBox()
        self.translation_type_combo.addItems(["Online", "Offline"])
        self.translation_type_combo.setCurrentText(self.translation_type)
        self.translation_type_combo.currentTextChanged.connect(self.update_translation_type)

        translation_type_layout.addWidget(translation_type_label)
        translation_type_layout.addWidget(self.translation_type_combo)
        options_page_layout.addLayout(translation_type_layout)

        # Add a stretch to push the back button to the bottom
        options_page_layout.addStretch(1)

        # Back Button
        self.back_button = QtWidgets.QPushButton('Back', self)
        self.back_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-size: 16px;
                background-color: #D3D3D3;
                color: black;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #C0C0C0;
            }
        """)
        self.back_button.clicked.connect(self.show_main)
        options_page_layout.addWidget(self.back_button)

        # Add pages to stacked widget
        self.stacked_widget.addWidget(self.main_page)
        self.stacked_widget.addWidget(self.options_page)

        self.setLayout(self.main_layout)
        logging.info("UI initialized.")

    def show_options(self):
        self.stacked_widget.setCurrentWidget(self.options_page)

    def show_main(self):
        self.stacked_widget.setCurrentWidget(self.main_page)

    def update_translation_type(self, new_type):
        self.translation_type = new_type
        logging.info(f"Translation type changed to: {self.translation_type}")

    def init_argos_translate(self):
        try:
            installed_languages = translate.get_installed_languages()
            logging.info(f"Installed languages: {[lang.code for lang in installed_languages]}")

            ja_lang = next((lang for lang in installed_languages if lang.code == 'ja'), None)

            if ja_lang:
                translations_to = [
                    getattr(lang, 'code', None) for lang in ja_lang.translations_to
                    if getattr(lang, 'code', None) is not None
                ]
                logging.info(f"Japanese translations available to: {translations_to}")

                if 'en' not in translations_to:
                    logging.info("Japanese to English model not found. Downloading...")
                    self.download_and_install_argos_package('ja', 'en')
                else:
                    logging.info("Japanese to English model is already installed.")
            else:
                logging.info("Japanese language not found. Downloading Japanese to English model...")
                self.download_and_install_argos_package('ja', 'en')

        except Exception as e:
            logging.exception("An error occurred while initializing Argos Translate.")

    def download_and_install_argos_package(self, from_code, to_code):
        try:
            installed_languages = translate.get_installed_languages()
            from_lang = next((lang for lang in installed_languages if lang.code == from_code), None)
            
            if from_lang:
                translations_to = [
                    getattr(lang, 'code', None) for lang in from_lang.translations_to
                    if getattr(lang, 'code', None) is not None
                ]
                if to_code in translations_to:
                    logging.info(f"{from_code} to {to_code} translation is already available.")
                    return  # Exit the method if the translation is already available

            # If we reach here, we need to download and install the package
            package.update_package_index()
            available_packages = package.get_available_packages()

            desired_package = next(
                (pkg for pkg in available_packages if pkg.from_code == from_code and pkg.to_code == to_code),
                None
            )

            if desired_package:
                download_path = desired_package.download()
                package.install_from_path(download_path)
                logging.info(f"Installed Argos Translate package: {from_code} to {to_code}")

                # Verify installation
                installed_languages = translate.get_installed_languages()
                from_lang = next((lang for lang in installed_languages if lang.code == from_code), None)
                if from_lang:
                    translations_to = [
                        getattr(lang, 'code', None) for lang in from_lang.translations_to
                        if getattr(lang, 'code', None) is not None
                    ]
                    if to_code in translations_to:
                        logging.info(f"{from_code} to {to_code} translation is now available.")
                    else:
                        logging.warning(f"{from_code} to {to_code} translation not found after installation. This may require investigation.")
                else:
                    logging.warning(f"Language {from_code} not found after installation. This may require investigation.")
            else:
                logging.error(f"No available Argos Translate package for {from_code} to {to_code}.")

        except Exception as e:
            logging.exception("An error occurred while managing Argos Translate package.")

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

    def perform_translation(self, image_bytes):
        if self.translation_type == "Offline":
            logging.info("Using offline translation.")
            return self.perform_offline_translation(image_bytes)
        else:
            logging.info("Using online translation (OpenAI API).")
            return self.call_openai_api(image_bytes)

    def call_openai_api(self, image_bytes):
        try:
            # Encode image to base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            image_data_url = f"data:image/png;base64,{base64_image}"
            logging.info("Image successfully encoded to base64.")

            # Prepare the messages with image
            messages = [
                {
                    "type": "text",
                    "text": """
                    Please extract any Japanese text from the image and translate it into English. 
                    Include only the translated text in your answer and do not give any context, and do not include any other text.
                    Do not include quotation marks in your response unless there are actual quotation marks in the text, or the equivalent of quotation marks in Japanese.
                    If there is no Japanese text in the image, or you are unable to translate it, please respond with "-Unable to translate-"
                    """
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

    def perform_offline_translation(self, image_bytes):
        try:
            # Use Pillow to open the image
            image = Image.open(io.BytesIO(image_bytes))

            # Use Tesseract to perform OCR
            japanese_text = pytesseract.image_to_string(image, lang='jpn')

            if not japanese_text.strip():
                return "-Unable to translate-"

            # Use Argos Translate to translate the text
            from_code = "ja"
            to_code = "en"

            # Load the translation model
            installed_languages = translate.get_installed_languages()
            from_lang = list(filter(lambda x: x.code == from_code, installed_languages))[0]
            to_lang = list(filter(lambda x: x.code == to_code, installed_languages))[0]
            translation = from_lang.get_translation(to_lang)

            # Perform the translation
            translated_text = translation.translate(japanese_text)

            logging.info("Offline translation completed successfully.")
            return translated_text
        except Exception as e:
            logging.exception("Error during offline translation.")
            return "-Unable to translate-"

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