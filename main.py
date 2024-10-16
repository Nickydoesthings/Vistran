import sys
import io
import os
import base64
import requests
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication, QGraphicsView, QGraphicsScene, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsDropShadowEffect, QTextEdit, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QLineEdit, QGridLayout, QComboBox
from PyQt5.QtGui import QIcon, QPainter, QColor, QPen
from PyQt5.QtCore import Qt, QRectF, QUrl, QTimer
from PIL import Image
import mss
import logging
import json
import keyring
import keyboard

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

API_URL = 'https://api.openai.com/v1/chat/completions'
MODEL_NAME = 'gpt-4o-mini'

MINIMUM_WINDOW_WIDTH = 70
MINIMUM_WINDOW_HEIGHT = 40 
MAX_RETRIES = 2 # Maximum number of retries for API calls

class SelectionWindow(QtWidgets.QWidget):
    selection_made = QtCore.pyqtSignal(QtCore.QRect)
    selection_cancelled = QtCore.pyqtSignal()  # New signal for cancellation

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
        elif event.button() == QtCore.Qt.RightButton:
            self.cancel_selection()

    def mouseMoveEvent(self, event):
        if self.rubberBand.isVisible():
            self.rubberBand.setGeometry(QtCore.QRect(self.origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.rubberBand.isVisible():
            self.rubberBand.hide()
            selected_rect = QtCore.QRect(self.origin, event.pos()).normalized()
            self.selection_made.emit(selected_rect)
            self.close()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancel_selection()

    def cancel_selection(self):
        self.selection_cancelled.emit()
        self.close()

    def closeEvent(self, event):
        logging.info("SelectionWindow is closing.")
        event.accept()

    def __del__(self):
        logging.info("SelectionWindow instance deleted.")

class TranslationDisplayWindow(QGraphicsView):
    def __init__(self, initial_text, rect, minimum_size):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        # Enforce minimum size
        adjusted_rect = self.adjust_rect_to_minimum_size(rect, minimum_size)
        self.setGeometry(adjusted_rect)

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

    def adjust_rect_to_minimum_size(self, rect, minimum_size):
        width = max(rect.width(), minimum_size)
        height = max(rect.height(), minimum_size)
        return QtCore.QRect(rect.x(), rect.y(), width, height)

class TranslationTask(QtCore.QRunnable):
    def __init__(self, img_bytes, translation_window, app_instance):
        super().__init__()
        self.img_bytes = img_bytes
        self.translation_window = translation_window
        self.app_instance = app_instance

    def run(self):
        # Perform the translation in the background
        detected_language, original_text, translated_text = self.app_instance.perform_translation(self.img_bytes)
        if detected_language and original_text and translated_text:
            logging.info("Translation successful.")
            # Emit the signal with detected language, original text, and translated text
            self.app_instance.translation_ready.emit(detected_language, original_text, translated_text)
        else:
            logging.error("Translation failed.")
            QtCore.QMetaObject.invokeMethod(
                self.app_instance,
                "show_error",
                QtCore.Qt.QueuedConnection
            )

class TranslatorApp(QtWidgets.QWidget):
    translation_ready = QtCore.pyqtSignal(str, str, str)  # Emits detected language, original text, and translated text

    def __init__(self):
        super().__init__()
        self.translation_windows = []
        self.target_language = "Autodetect"
        self.init_ui()
        self.translation_ready.connect(self.update_translation_display)
        self.init_hotkey()
        self.selection_window = None  # Initialize selection_window attribute
        self.target_language = "Autodetect"

    def init_ui(self):
        logging.info("Initializing UI.")
        self.setWindowTitle('Vistran: Visual Translator')
        self.setGeometry(100, 100, 400, 400)  # Adjusted size for better layout

        # Set the main window style
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                border-radius: 10px;
            }
            QLabel {
                font-weight: bold;
                font-size: 14px;
                color: #333;
            }
            QPushButton {
                padding: 10px 20px;
                font-size: 16px;
                border: none;
                border-radius: 5px;
            }
            QTextEdit {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                padding: 5px;
                font-size: 14px;
            }
        """)

        # Main Layout
        self.main_layout = QtWidgets.QVBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        # Create a stacked widget to hold different pages
        self.stacked_widget = QtWidgets.QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget)

        # Create main page
        self.main_page = QtWidgets.QWidget()
        main_page_layout = QtWidgets.QVBoxLayout(self.main_page)
        main_page_layout.setSpacing(10)

        # Capture Button
        self.capture_button = QtWidgets.QPushButton('Capture Screenshot', self)
        self.capture_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.capture_button.clicked.connect(self.capture_screenshot)
        main_page_layout.addWidget(self.capture_button)

        # Create text display areas
        text_display_layout = QGridLayout()
        text_display_layout.setVerticalSpacing(2)
        text_display_layout.setHorizontalSpacing(10)

        # Detected language and text box
        self.detected_label = QLabel("Detected Language:") 
        self.detected_text_display = QTextEdit(self)
        self.detected_text_display.setReadOnly(True)
        self.detected_text_display.setMinimumHeight(100)

        # Translation text box
        translation_label = QLabel("Translation:")
        self.translation_text_display = QTextEdit(self)
        self.translation_text_display.setReadOnly(True)
        self.translation_text_display.setMinimumHeight(100)

        # Add widgets to the grid
        text_display_layout.addWidget(self.detected_label, 0, 0)
        text_display_layout.addWidget(self.detected_text_display, 1, 0)
        text_display_layout.addWidget(translation_label, 3, 0)
        text_display_layout.addWidget(self.translation_text_display, 4, 0)

        # Add a spacer item between detected and translation sections
        spacer_item = QtWidgets.QSpacerItem(20, 10, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        text_display_layout.addItem(spacer_item, 2, 0)

        # Set row stretches to make text boxes expand, not labels
        text_display_layout.setRowStretch(1, 1)
        text_display_layout.setRowStretch(4, 1)

        main_page_layout.addLayout(text_display_layout)
        main_page_layout.setStretchFactor(text_display_layout, 1)

        # Options Button
        self.options_button = QtWidgets.QPushButton('Options', self)
        self.options_button.setStyleSheet("""
            QPushButton {
                background-color: #6C757D;
                color: white;
            }
            QPushButton:hover {
                background-color: #5A6268;
            }
        """)
        self.options_button.clicked.connect(self.show_options)
        main_page_layout.addWidget(self.options_button)

        # Create options page
        self.options_page = QtWidgets.QWidget()
        options_page_layout = QtWidgets.QVBoxLayout(self.options_page)
        options_page_layout.setSpacing(15)

        # API Key Input
        api_key_layout = QtWidgets.QVBoxLayout()
        api_key_input_layout = QtWidgets.QHBoxLayout()
        api_key_label = QtWidgets.QLabel("OpenAI API Key:")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.load_api_key())
        self.api_key_input.textChanged.connect(self.save_api_key)
        self.api_key_input.setStyleSheet("""
            QLineEdit {
                padding: 5px;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
        """)
        
        self.api_key_toggle = QtWidgets.QPushButton("Show")
        self.api_key_toggle.setCheckable(True)
        self.api_key_toggle.toggled.connect(self.toggle_api_key_visibility)
        self.api_key_toggle.setStyleSheet("""
            QPushButton {
                padding: 5px 10px;
                background-color: #f0f0f0;
                color: #333;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
        """)

        api_key_input_layout.addWidget(api_key_label)
        api_key_input_layout.addWidget(self.api_key_input)
        api_key_input_layout.addWidget(self.api_key_toggle)
        
        api_key_layout.addLayout(api_key_input_layout)

        # Add the "What is an API key?" link
        api_key_link = QtWidgets.QLabel()
        api_key_link.setText('<a href="https://help.openai.com/en/articles/7039783-how-can-i-access-the-chatgpt-api">What is an API key?</a>')
        api_key_link.setOpenExternalLinks(True)
        api_key_link.setStyleSheet("color: blue;")
        api_key_layout.addWidget(api_key_link)

        options_page_layout.addLayout(api_key_layout)

        # Target Language Selection
        target_language_layout = QtWidgets.QHBoxLayout()
        target_language_label = QtWidgets.QLabel("Target Language:")
        self.target_language_combo = QComboBox()
        self.target_language_combo.addItems([
            "Autodetect",
            "Arabic",
            "Bengali",
            "Chinese (Simplified)",
            "Chinese (Traditional)",
            "Danish",
            "Dutch",
            "Finnish",
            "French",
            "German",
            "Greek",
            "Hindi",
            "Italian",
            "Japanese",
            "Korean",
            "Norwegian",
            "Polish",
            "Portuguese (Brazilian)",
            "Portuguese (European)",
            "Russian",
            "Spanish",
            "Swedish",
            "Turkish"
        ])
        self.target_language_combo.setCurrentText(self.target_language)
        self.target_language_combo.currentTextChanged.connect(self.update_target_language)
        
        target_language_layout.addWidget(target_language_label)
        target_language_layout.addWidget(self.target_language_combo)
        options_page_layout.addLayout(target_language_layout)

        # Add a stretch to push the back button to the bottom
        options_page_layout.addStretch(1)

        # Back Button
        self.back_button = QtWidgets.QPushButton('Back', self)
        self.back_button.setStyleSheet("""
            QPushButton {
                background-color: #6C757D;
                color: white;
            }
            QPushButton:hover {
                background-color: #5A6268;
            }
        """)
        self.back_button.clicked.connect(self.show_main)
        options_page_layout.addWidget(self.back_button)

        # Add pages to stacked widget
        self.stacked_widget.addWidget(self.main_page)
        self.stacked_widget.addWidget(self.options_page)

        self.setLayout(self.main_layout)
        logging.info("UI initialized.")

        # Set the minimum size of the window
        self.setMinimumSize(400, 400)

    def show_options(self):
        self.stacked_widget.setCurrentWidget(self.options_page)

    def show_main(self):
        self.stacked_widget.setCurrentWidget(self.main_page)

    def toggle_api_key_visibility(self, checked):
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.api_key_toggle.setText("Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.api_key_toggle.setText("Show")

    def save_api_key(self, api_key):
        keyring.set_password("VisualTranslator", "openai_api_key", api_key)

    def load_api_key(self):
        return keyring.get_password("VisualTranslator", "openai_api_key") or ""

    def init_hotkey(self):
        try:
            keyboard.add_hotkey('ctrl+alt+space', self.hotkey_triggered)
            logging.info("Hotkey (Ctrl+Alt+Space) registered successfully.")
        except Exception as e:
            logging.error(f"Failed to register hotkey: {e}")

    def hotkey_triggered(self):
        logging.info("Hotkey triggered. Initiating screenshot capture.")
        # Use QTimer to call capture_screenshot from the main thread
        QTimer.singleShot(0, self.capture_screenshot)

    def capture_screenshot(self):
        try:
            logging.info("Starting screenshot capture.")
            
            if self.selection_window is None:
                self.selection_window = SelectionWindow()
                self.selection_window.selection_made.connect(self.on_selection_made)
                self.selection_window.selection_cancelled.connect(self.on_selection_cancelled)  # New connection
            
            self.selection_window.show()
            self.selection_window.activateWindow()  # Ensure the selection window is in focus
        except Exception as e:
            logging.exception("Failed to initiate screenshot capture.")

    def on_selection_cancelled(self):
        logging.info("Screenshot selection cancelled by user.")
        if self.selection_window:
            self.selection_window.close()
            self.selection_window.deleteLater()
            self.selection_window = None

    def on_selection_made(self, rect):
        try:
            logging.info(f"User selected rectangle: {rect}")
            self.selected_rect = rect
            # Use mss to capture the selected region
            with mss.mss() as sct:
                monitor = {
                    "left": rect.left(),
                    "top": rect.top(),
                    "width": max(rect.width(), MINIMUM_WINDOW_WIDTH),
                    "height": max(rect.height(), MINIMUM_WINDOW_HEIGHT)
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
            self.translation_window = TranslationDisplayWindow("Translating...", self.selected_rect, MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
            self.translation_window.show()
            self.translation_windows.append(self.translation_window)

            # Now process the image and update the window with the actual translation
            self.process_image(img)
        except Exception as e:
            logging.exception("Failed during screenshot processing.")

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
        logging.info("Using online translation (OpenAI API).")
        api_key = self.load_api_key()
        if not api_key:
            logging.error("No API key provided")
            return "No API key provided", "No API key provided", "No API key provided"
        for attempt in range(MAX_RETRIES):
            detected_language, original_text, english_text = self.call_openai_api(image_bytes, api_key)
            if not (detected_language.startswith("API") and original_text.startswith("API") and english_text.startswith("API")):
                return detected_language, original_text, english_text
            logging.warning(f"API call failed. Attempt {attempt + 1} of {MAX_RETRIES}")
        logging.error("All API call attempts failed")
        return "All API call attempts failed", "All API call attempts failed", "All API call attempts failed"

    def call_openai_api(self, image_bytes, api_key):
        try:
            # Encode image to base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            image_data_url = f"data:image/png;base64,{base64_image}"
            logging.info("Image successfully encoded to base64.")

            # Prepare the messages with image and target language
            target_language_prompt = f"The target language is {self.target_language}. " if self.target_language != "Autodetect" else ""
            messages = [
                {
                    "type": "text",
                    "text": f"""
                    {target_language_prompt}Please extract any text from the image, detect its language, and translate it into English. 
                    Provide your response in the following JSON format:
                    {{
                        "detected_language": "The detected language",
                        "original_text": "The original text in the detected language",
                        "english_translation": "The English translation"
                    }}
                    If there is no text in the image, or you are unable to translate it, 
                    please respond with:
                    {{
                        "detected_language": "Unable to detect",
                        "original_text": "-Unable to extract-",
                        "english_translation": "-Unable to translate-"
                    }}
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
                "Authorization": f"Bearer {api_key}"
            }

            logging.info("Sending request to OpenAI API.")
            response = requests.post(API_URL, headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                
                # Log the raw API response for debugging
                logging.debug(f"Raw API response: {result}")
                
                try:
                    # Extract the content from the API response
                    content = result['choices'][0]['message']['content']
                    
                    # Remove Markdown code block formatting if present
                    content = content.strip('`')
                    if content.startswith('json\n'):
                        content = content[5:]  # Remove 'json\n'
                    
                    # Parse the content as JSON
                    translation_data = json.loads(content)
                    logging.info("Received successful response from OpenAI API.")
                    return (
                        translation_data.get('detected_language', 'Unable to detect'),
                        translation_data.get('original_text', '-Unable to extract-'),
                        translation_data.get('english_translation', '-Unable to translate-')
                    )
                except json.JSONDecodeError as e:
                    logging.error(f"Failed to parse API response as JSON: {e}")
                    logging.error(f"Response content: {content}")
                    return "API parsing error", "API parsing error", "API parsing error"
                except KeyError as e:
                    logging.error(f"Unexpected API response structure: {e}")
                    logging.error(f"Response content: {result}")
                    return "API structure error", "API structure error", "API structure error"
            else:
                logging.error(f"API Error: {response.status_code}, {response.text}")
                return f"API error: {response.status_code}", f"API error: {response.status_code}", f"API error: {response.status_code}"
        except Exception as e:
            logging.exception("Exception occurred during API call.")
            return f"API call error: {str(e)}", f"API call error: {str(e)}", f"API call error: {str(e)}"

    @QtCore.pyqtSlot()
    def show_error(self):
        QtWidgets.QMessageBox.critical(self, "Error", "Failed to get translation.")

    @QtCore.pyqtSlot(str, str, str)
    def update_translation_display(self, detected_language, original_text, translated_text):
        if self.translation_window:
            self.translation_window.update_text(translated_text)
        
        # Update the detected language label
        self.detected_label.setText(f"Detected Language: {detected_language}")
        
        # Update the text displays in the main window
        self.detected_text_display.setText(original_text)
        self.translation_text_display.setText(translated_text)
        
        # Log the displayed text for debugging
        logging.debug(f"Detected Language: {detected_language}")
        logging.debug(f"Original Text: {original_text}")
        logging.debug(f"Translated Text: {translated_text}")

    def update_target_language(self, language):
        self.target_language = language

class TranslationDisplayWindow(QGraphicsView):
    def __init__(self, initial_text, rect, minimum_width, minimum_height):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        # Enforce minimum size
        adjusted_rect = self.adjust_rect_to_minimum_size(rect, minimum_width, minimum_height)
        self.setGeometry(adjusted_rect)

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

        # Add drop shadow effect to the text
        shadow_effect = QGraphicsDropShadowEffect()
        shadow_effect.setBlurRadius(5)
        shadow_effect.setOffset(2, 2)
        shadow_effect.setColor(QColor(0, 0, 0, 50))
        self.text_item.setGraphicsEffect(shadow_effect)

        # Center the text
        self.center_text()

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

    def center_text(self):
        text_rect = self.text_item.boundingRect()
        self.text_item.setPos((self.width() - text_rect.width()) / 2, (self.height() - text_rect.height()) / 2)

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
        max_font_size = 72 
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

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(200, 200, 200, 100))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

    def wheelEvent(self, event):
        # Override to disable scrolling with the mouse wheel
        event.ignore()

    def adjust_rect_to_minimum_size(self, rect, minimum_width, minimum_height):
        width = max(rect.width(), minimum_width)
        height = max(rect.height(), minimum_height)
        return QtCore.QRect(rect.x(), rect.y(), width, height)

    # ... rest of the existing methods ...

def main():
    logging.info("Starting Visual Translator application.")
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QIcon('images/v-letter.svg'))
    translator = TranslatorApp()
    translator.show()
    
    # Keep the application running in the background with the below. Works even if you close the window. Not sure why you'd want this but here it is.
    # app.setQuitOnLastWindowClosed(False)
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()