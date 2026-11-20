# Recorded-run image (docs/03-build-spec.md section 5). The base is pinned by digest
# in docker/base.digest; benchmark/record_demo.py builds with that digest and
# records the resulting image ID in every recorded report.
ARG BASE
FROM ${BASE}
RUN python -m pip install --no-cache-dir "pytest==8.3.3"
