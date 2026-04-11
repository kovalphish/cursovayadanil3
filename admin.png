import base64

# Создаем простое изображение 1x1 пиксель серого цвета в формате PNG
png_data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
with open('static/default.png', 'wb') as f:
    f.write(png_data)

# Конвертируем в base64 для использования в коде
with open('static/default.png', 'rb') as f:
    image_base64 = base64.b64encode(f.read()).decode('utf-8')
    print(f"data:image/png;base64,{image_base64}")