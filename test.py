import os
import pytesseract
from PIL import Image

os.environ['TESSDATA_PREFIX'] = r'C:\Program Files\Tesseract-OCR\tessdata'
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Use a raw string (r prefix) for the image path
image_path = r'C:\Users\nicky\OneDrive\Desktop\download.jpeg'  # Replace with an actual image path
image = Image.open(image_path)

tessdata_dir = r'C:\Program Files\Tesseract-OCR\tessdata'
config = f'--tessdata-dir "{tessdata_dir}" --psm 6 --oem 1'

try:
    text = pytesseract.image_to_string(image, lang='jpn', config=config)
    print("Extracted text:", text)
except Exception as e:
    print("Error:", str(e))