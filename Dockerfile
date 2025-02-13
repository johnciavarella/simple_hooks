# Use an official Python runtime as a parent image
FROM python:3.13.1-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .
COPY webserver.py .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Git and other necessary packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Run the Python script when the container launches
CMD ["sh", "-c", "python webserver.py --git-repo-dir $GIT_REPO_DIR"]