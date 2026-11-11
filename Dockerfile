FROM python:3.13-alpine
COPY foxess_exporter.py /app/foxess_exporter.py
USER nobody
EXPOSE 9877
CMD ["python3", "-u", "/app/foxess_exporter.py"]
