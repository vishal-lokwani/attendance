# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the current directory contents into the container
COPY requirements.txt /app/
COPY . /app/

# Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 10000 (Render default port)
EXPOSE 10000

# Define environment variable for Flask
ENV FLASK_APP=app.py

# Allow Flask to run on any IP address and Render's default port
CMD ["flask", "run", "--host=0.0.0.0", "--port=10000"]
