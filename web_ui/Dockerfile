# Use Node.js 20 Alpine for smaller image
FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the application
RUN npm run build

# Expose the port (will be configurable)
EXPOSE 725

# Start the application
CMD ["sh", "-c", "npm run preview -- --host 0.0.0.0 --port ${WEB_UI_PORT:-725}"]