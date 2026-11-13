# Official Playwright image: Python + Chromium + all system libraries preinstalled.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
