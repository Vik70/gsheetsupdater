# Use a full Python image to ensure all standard libraries are available
FROM python:3.9-buster

# Set the working directory in the container
WORKDIR /app

# Confirm the Python version being used
RUN python --version

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Set the command to run your Discord bot, explicitly using python3.9
CMD ["python3.9", "discord_bot.py"] 