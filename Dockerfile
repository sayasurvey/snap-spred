FROM --platform=linux/arm64 python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
EXPOSE 8501
CMD ["streamlit", "run", "app/main.py", "--server.address", "0.0.0.0"]
