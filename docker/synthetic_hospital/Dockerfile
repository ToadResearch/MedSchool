FROM python:3.12-slim
WORKDIR /app
COPY export_fhir.py .
ENTRYPOINT ["python", "export_fhir.py", "--download"]
