FROM python:3.13-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY foxess_exporter.py /app/foxess_exporter.py
USER 568:568
EXPOSE 9877
CMD ["python3", "/app/foxess_exporter.py"]
