from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/health")
def read_root():
    return {"message": "Hello from FastAPI!"}

def main():
    print("Hello from backend!")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()