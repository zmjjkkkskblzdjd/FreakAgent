import os

os.environ["AF_VECTOR_BACKEND"] = os.getenv("AF_TEST_VECTOR_BACKEND", "chroma")
