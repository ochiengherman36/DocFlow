import os

os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["ANTHROPIC_MODEL"] = "test-model"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000"
os.environ["MAX_UPLOAD_MB"] = "1"