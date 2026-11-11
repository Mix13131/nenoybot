FROM python:3.12-slim
WORKDIR /srv/nenoy
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app_v2/ ./app_v2/
CMD ["python", "-m", "app_v2.labs.telegram_lab"]
