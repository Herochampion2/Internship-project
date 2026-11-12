# syntax=docker/dockerfile:1
#
# Container image for the fleet compliance pipeline.
#
#   docker build -t fleet-compliance .
#   docker run --rm -v "$PWD/output:/app/output" fleet-compliance
#   docker run --rm fleet-compliance --webhook http://host.docker.internal:8080/v1/compliance/webhook
#
# Image vs container vs volume, in one line each: the image is this built,
# read-only filesystem snapshot; the container is one running process on top of
# it; the volume above is what carries output/ back out, because a container's
# own writes disappear with it.

FROM python:3.12-slim

# Unbuffered output so the stage log appears live in `docker logs`; no .pyc
# files inside the image; no pip download cache baked into a layer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first. Nothing above this line changes between code edits, so
# the pip layer is reused from cache on every rebuild; only a change to a
# requirements file busts it. Code is copied afterwards, which is why editing
# pipeline.py rebuilds in under a second instead of re-resolving pandas.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code last -- the layer that changes on every commit.
COPY pyproject.toml run_pipeline.py ./
COPY fleet_compliance/ ./fleet_compliance/
COPY tools/ ./tools/

# Run as an unprivileged user. The pipeline writes only to data/ (self-generated
# demo inputs) and output/ (artefacts), so exactly those two are handed over.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/output \
    && chown -R appuser:appuser /app
USER appuser

# Default is the self-contained demo: mock inputs are generated, 1,187 records
# are classified and both artefacts are written. Network dispatch is off unless
# a --webhook URL is passed at run time.
ENTRYPOINT ["python", "run_pipeline.py"]
CMD ["--no-webhook"]
