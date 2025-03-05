# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements first
COPY ./requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the project files
COPY . /app/

# Expose Render's default port
EXPOSE 10000

# Environment variable for Flask
ENV FLASK_APP=app.py

# Start Flask app
CMD ["flask", "run", "--host=0.0.0.0", "--port=10000"]
