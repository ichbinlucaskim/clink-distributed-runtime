# 1. Base Image: Use Python 3.11 (Matching your proposal)
FROM python:3.11-slim

# 2. Set Working Directory
WORKDIR /app

# 3. Install System Dependencies (Minimal)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# 4. Install Poetry
RUN pip install poetry

# 5. Copy Dependency Files
COPY pyproject.toml poetry.lock ./

# 6. Update lock file if needed and Install Python Dependencies
# --no-root: Do not install the project itself (since we mounted source code)
# --no-interaction: No questions asked
RUN poetry config virtualenvs.create false && \
    poetry lock --no-interaction --no-ansi && \
    poetry install --no-root --no-interaction --no-ansi

# 7. Copy Source Code
COPY . .

# 8. Command to Run the Application
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]