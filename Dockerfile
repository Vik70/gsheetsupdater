# Use a full Python image to ensure all standard libraries are available
FROM python:3.9-buster

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Set the command to run your Discord bot
CMD ["python", "discord_bot.py"] 