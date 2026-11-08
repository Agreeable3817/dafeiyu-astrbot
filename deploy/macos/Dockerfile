# syntax=docker/dockerfile:1
ARG ASTRBOT_BASE=soulter/astrbot@sha256:66ae67e93be2996f9aaef29d90f659c0b7f1d245a45cda8bea40ebdd0f34d943
FROM ${ASTRBOT_BASE}
RUN --mount=type=secret,id=pip_ca \
    --mount=type=bind,source=requirements.txt,target=/opt/dafeiyu-requirements.txt,readonly \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    python -m pip install --no-cache-dir -r /opt/dafeiyu-requirements.txt \
    && python -m pip check \
    && python -c "import faiss, numpy, jieba, aiosqlite, dashscope, imageio_ffmpeg; assert faiss.IndexFlatIP(1024).d == 1024"
