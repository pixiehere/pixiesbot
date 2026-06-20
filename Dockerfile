FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg libsodium23 libsodium-dev gcc && \
    apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "discord.py[voice]" PyNaCl==1.5.0

COPY . .

CMD ["python", "bot.py"]
