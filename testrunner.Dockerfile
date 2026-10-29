# R8 clean-room test image: stock python slim + a pinned pytest (offline at run time).
# Build once per host: docker build -f testrunner.Dockerfile -t rouvr-runner:py313-p9 .
FROM python:3.13-slim
RUN pip install --no-cache-dir "pytest==9.1.1"
