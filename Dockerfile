# Используем официальный Python-образ
FROM python:3.12-slim

# Установка зависимостей
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .

# Указываем переменные окружения
ENV PYTHONUNBUFFERED=1

# Стартовый скрипт
CMD ["python", "bot.py"]