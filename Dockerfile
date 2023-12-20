# Use an official Python runtime as the base image
FROM python:3.12
# Set the working directory in the container
WORKDIR /app

# Copy the requirements file to the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code to the container
COPY import.py .

# Run the Python app
CMD ["python", "import.py"]
