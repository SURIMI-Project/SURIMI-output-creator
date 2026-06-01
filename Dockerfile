# Use the official Python image from DockerHub
FROM python:3.12-slim

# Upgrade system packages to address vulnerabilities
RUN apt-get update && apt-get upgrade -y && apt-get clean

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements.txt file into the container
COPY requirements.txt /app/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all the application files into the container
COPY . /app

# Add the /app folder to PYTHONPATH so that the `surimi` module is found
ENV PYTHONPATH=/app

# Expose server port
EXPOSE 5189

ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
ENV OTEL_SERVICE_NAME=market

# Run the server -u option is used to ensure that output (logs) is flushed immediately
CMD ["python", "-u", "server/app.py"]