import uvicorn
from yuxi.api import app

def main():
    print("Hello from yuxi!")


if __name__ == "__main__":
    main()
    #uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
